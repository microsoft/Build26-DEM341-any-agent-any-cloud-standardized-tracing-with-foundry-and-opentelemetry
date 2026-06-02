"""OpenTelemetry plumbing for the Foundry-hosted orchestrator.

Extracted from ``main.py`` so the orchestrator module stays focused on routing
and specialist calls. Two responsibilities live here:

1. **Import-time GenAI tracing env defaults.** Setting these must happen before
   ``agent_framework`` / the Azure SDKs are imported, so ``main.py`` imports this
   module first.
2. **One-shot startup wiring** via :func:`setup_observability`, which installs
   the ``AIProjectInstrumentor``, silences Agent Framework plumbing spans, drops
   host-infrastructure noise spans, and registers the root-agent span tracker.

The public span helpers (``_set_span_input_message``, ``_start_demo_root_agent_span``,
the suppression context helpers, etc.) keep their original names so call sites in
``main.py`` are unchanged.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import TypeVar


def _append_otel_excluded_urls(*patterns: str) -> None:
    value = ",".join(patterns)
    for key in (
        "OTEL_PYTHON_EXCLUDED_URLS",
        "OTEL_PYTHON_REQUESTS_EXCLUDED_URLS",
        "OTEL_PYTHON_URLLIB_EXCLUDED_URLS",
        "OTEL_PYTHON_URLLIB3_EXCLUDED_URLS",
        "OTEL_PYTHON_HTTPX_EXCLUDED_URLS",
        "OTEL_PYTHON_AIOHTTP_CLIENT_EXCLUDED_URLS",
    ):
        current = os.environ.get(key)
        os.environ[key] = f"{current},{value}" if current else value


os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
os.environ.setdefault(
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
    "SPAN_AND_EVENT",
)
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
_append_otel_excluded_urls(
    r".*/msi/token.*",
    r".*/metadata/identity/oauth2/token.*",
    r".*/storage/history/item_ids.*",
    r".*/storage/responses.*",
)

from azure.core.tracing.ext import opentelemetry_span as _azure_core_otel_span
from opentelemetry import context as otel_context, trace
from opentelemetry.trace import SpanKind

try:
    from opentelemetry.context import (
        _SUPPRESS_HTTP_INSTRUMENTATION_KEY,
        _SUPPRESS_INSTRUMENTATION_KEY,
    )
except ImportError:  # pragma: no cover
    _SUPPRESS_HTTP_INSTRUMENTATION_KEY = "suppress_http_instrumentation"
    _SUPPRESS_INSTRUMENTATION_KEY = "suppress_instrumentation"

_T = TypeVar("_T")
_AZURE_CORE_SUPPRESSED_SPAN_FLAG = getattr(
    _azure_core_otel_span,
    "_SUPPRESSED_SPAN_FLAG",
    "SUPPRESSED_SPAN_FLAG",
)

_SERVICE_NAME = "orchestrator"
tracer = trace.get_tracer(_SERVICE_NAME)

_ROOT_AGENT_SPANS: dict[int, object] = {}
_ROOT_AGENT_SPANS_LOCK = threading.Lock()
_ROOT_SPAN_REGISTRY_INSTALLED = False
_NOISE_FILTER_SAMPLER_INSTALLED = False


def configure(service_name: str) -> None:
    """Bind the service name used for root-agent span naming and the tracer."""
    global _SERVICE_NAME, tracer
    _SERVICE_NAME = service_name
    tracer = trace.get_tracer(service_name)


# --- Span context + classification helpers ---------------------------------
def _span_context(span: object):
    get_span_context = getattr(span, "get_span_context", None)
    if callable(get_span_context):
        return get_span_context()
    return getattr(span, "context", None)


def _is_orchestrator_root_span(span: object) -> bool:
    name = getattr(span, "name", "")
    if isinstance(name, str) and name.startswith(f"invoke_agent {_SERVICE_NAME}"):
        return True
    if getattr(span, "kind", None) != SpanKind.SERVER:
        return False
    attrs = getattr(span, "attributes", {}) or {}
    method = attrs.get("http.request.method") or attrs.get("http.method")
    route = attrs.get("http.route") or attrs.get("url.path") or attrs.get("http.target")
    return method == "POST" and (
        name == "POST /responses" or route == "/responses" or str(route).endswith("/responses")
    )


def _span_match_text(name: str, attributes: object | None) -> str:
    parts = [name]
    if isinstance(attributes, dict):
        for key in (
            "url.full",
            "http.url",
            "http.target",
            "http.route",
            "url.path",
            "server.address",
            "net.peer.name",
            "peer.service",
        ):
            value = attributes.get(key)
            if value is not None:
                parts.append(str(value))
    return " ".join(parts)


def _is_noisy_infra_span(name: str, attributes: object | None) -> bool:
    text = _span_match_text(name, attributes)
    return any(
        marker in text
        for marker in (
            "/msi/token",
            "/metadata/identity/oauth2/token",
            "/storage/history/item_ids",
            "/storage/responses",
        )
    )


def _install_noise_filter_sampler() -> None:
    """Drop host infrastructure spans before they reach Azure Monitor."""
    global _NOISE_FILTER_SAMPLER_INSTALLED
    if _NOISE_FILTER_SAMPLER_INSTALLED:
        return

    try:
        from opentelemetry.sdk.trace import SpanProcessor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import Decision, SamplingResult, Sampler
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning("Noise sampler unavailable: %s", e)
        return

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return

    current_sampler = getattr(provider, "sampler", None)
    if current_sampler is None or current_sampler.__class__.__name__ == "_NoiseFilterSampler":
        _NOISE_FILTER_SAMPLER_INSTALLED = True
        return

    class _NoiseFilterSampler(Sampler):
        def __init__(self, delegate: Sampler) -> None:
            self._delegate = delegate

        def should_sample(
            self,
            parent_context,
            trace_id,
            name,
            kind=None,
            attributes=None,
            links=None,
            trace_state=None,
        ) -> SamplingResult:
            if _is_noisy_infra_span(str(name), attributes):
                return SamplingResult(Decision.DROP)
            return self._delegate.should_sample(
                parent_context,
                trace_id,
                name,
                kind,
                attributes,
                links,
                trace_state,
            )

        def get_description(self) -> str:
            return f"NoiseFilterSampler({self._delegate.get_description()})"

    provider.sampler = _NoiseFilterSampler(current_sampler)
    active_processor = getattr(provider, "_active_span_processor", None)
    if (
        active_processor is not None
        and active_processor.__class__.__name__ != "_NoiseFilterSpanProcessor"
    ):

        class _NoiseFilterSpanProcessor(SpanProcessor):
            def __init__(self, delegate: SpanProcessor) -> None:
                self._delegate = delegate

            @staticmethod
            def _should_drop(span: object) -> bool:
                name = str(getattr(span, "name", ""))
                attributes = getattr(span, "attributes", None)
                return _is_noisy_infra_span(name, attributes)

            def on_start(self, span, parent_context=None) -> None:
                self._delegate.on_start(span, parent_context=parent_context)

            def _on_ending(self, span) -> None:
                if not self._should_drop(span):
                    self._delegate._on_ending(span)

            def on_end(self, span) -> None:
                if not self._should_drop(span):
                    self._delegate.on_end(span)

            def shutdown(self) -> None:
                self._delegate.shutdown()

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return self._delegate.force_flush(timeout_millis)

        provider._active_span_processor = _NoiseFilterSpanProcessor(active_processor)
    _NOISE_FILTER_SAMPLER_INSTALLED = True


def _install_root_span_registry() -> None:
    global _ROOT_SPAN_REGISTRY_INSTALLED
    if _ROOT_SPAN_REGISTRY_INSTALLED:
        return

    try:
        from opentelemetry.sdk.trace import SpanProcessor
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "Root span registry unavailable: %s", e
        )
        return

    class _RootAgentSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None) -> None:  # noqa: ANN001
            if not _is_orchestrator_root_span(span):
                return
            ctx = _span_context(span)
            trace_id = getattr(ctx, "trace_id", 0)
            if trace_id:
                with _ROOT_AGENT_SPANS_LOCK:
                    _ROOT_AGENT_SPANS[trace_id] = span

        def on_end(self, span) -> None:  # noqa: ANN001
            if not _is_orchestrator_root_span(span):
                return
            ctx = _span_context(span)
            trace_id = getattr(ctx, "trace_id", 0)
            if trace_id:
                with _ROOT_AGENT_SPANS_LOCK:
                    _ROOT_AGENT_SPANS.pop(trace_id, None)

        def shutdown(self) -> None:
            with _ROOT_AGENT_SPANS_LOCK:
                _ROOT_AGENT_SPANS.clear()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    provider = trace.get_tracer_provider()
    add_processor = getattr(provider, "add_span_processor", None)
    if callable(add_processor):
        add_processor(_RootAgentSpanProcessor())
        _ROOT_SPAN_REGISTRY_INSTALLED = True


def _current_root_agent_span():
    current_ctx = trace.get_current_span().get_span_context()
    trace_id = getattr(current_ctx, "trace_id", 0)
    if trace_id:
        with _ROOT_AGENT_SPANS_LOCK:
            span = _ROOT_AGENT_SPANS.get(trace_id)
        if span is not None:
            return span
    return trace.get_current_span()


def _start_demo_root_agent_span(query: str):
    span = tracer.start_span(
        f"invoke_agent {_SERVICE_NAME}",
        kind=SpanKind.SERVER,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": _SERVICE_NAME,
            "demo.input.content": query,
            "demo.input.length": len(query),
        },
    )
    ctx = _span_context(span)
    trace_id = getattr(ctx, "trace_id", 0)
    if trace_id:
        with _ROOT_AGENT_SPANS_LOCK:
            _ROOT_AGENT_SPANS[trace_id] = span
    return span


# --- GenAI input/output message helpers ------------------------------------
def _genai_messages_json(role: str, content: str) -> str:
    import json

    return json.dumps(
        [{"role": role, "parts": [{"type": "text", "content": content}]}]
    )


def _is_recording(span: object) -> bool:
    is_recording = getattr(span, "is_recording", None)
    return bool(callable(is_recording) and is_recording())


def _set_span_input_message(span: object, content: str) -> None:
    if not _is_recording(span):
        return
    span.set_attribute("demo.input.content", content)
    span.set_attribute("demo.input.length", len(content))
    span.set_attribute("gen_ai.input.messages", _genai_messages_json("user", content))


def _set_span_output_message(span: object, content: str) -> None:
    if not _is_recording(span):
        return
    span.set_attribute("demo.output.content", content)
    span.set_attribute("demo.output.length", len(content))
    span.set_attribute(
        "gen_ai.output.messages", _genai_messages_json("assistant", content)
    )


def _add_root_user_message(query: str) -> None:
    span = _current_root_agent_span()
    if not _is_recording(span):
        return
    span.set_attribute("gen_ai.operation.name", "invoke_agent")
    span.set_attribute("gen_ai.agent.name", _SERVICE_NAME)
    _set_span_input_message(span, query)
    span.add_event(
        "gen_ai.user.message",
        attributes={
            "gen_ai.system": "az.ai.foundry",
            "gen_ai.message.content": query,
        },
    )


def _add_root_choice(output: str) -> None:
    span = _current_root_agent_span()
    if not _is_recording(span):
        return
    _set_span_output_message(span, output)
    span.add_event(
        "gen_ai.choice",
        attributes={
            "gen_ai.system": "az.ai.foundry",
            "gen_ai.message.content": output,
        },
    )
    if getattr(span, "name", "") == f"invoke_agent {_SERVICE_NAME}":
        ctx = _span_context(span)
        trace_id = getattr(ctx, "trace_id", 0)
        span.end()
        if trace_id:
            with _ROOT_AGENT_SPANS_LOCK:
                _ROOT_AGENT_SPANS.pop(trace_id, None)


# --- Trace-header + instrumentation-suppression helpers --------------------
def _current_trace_id_hex() -> str:
    ctx = trace.get_current_span().get_span_context()
    if not ctx or not ctx.trace_id:
        return ""
    return format(ctx.trace_id, "032x")


def _preserve_demo_trace_headers(headers: dict[str, str]) -> None:
    traceparent = headers.get("traceparent")
    if traceparent:
        headers["x-demo-traceparent"] = traceparent
    tracestate = headers.get("tracestate")
    if tracestate:
        headers["x-demo-tracestate"] = tracestate


def _without_azure_core_span_suppression(call: Callable[[], _T]) -> _T:
    """Run Azure SDK client work with Azure Core internal span creation enabled."""
    current = otel_context.get_current()
    if not otel_context.get_value(_AZURE_CORE_SUPPRESSED_SPAN_FLAG, current):
        return call()

    token = otel_context.attach(
        otel_context.set_value(_AZURE_CORE_SUPPRESSED_SPAN_FLAG, False, current)
    )
    try:
        return call()
    finally:
        otel_context.detach(token)


def _without_http_auto_instrumentation(call: Callable[[], _T]) -> _T:
    current = otel_context.get_current()
    ctx = otel_context.set_value(_SUPPRESS_HTTP_INSTRUMENTATION_KEY, True, current)
    ctx = otel_context.set_value(_SUPPRESS_INSTRUMENTATION_KEY, True, ctx)
    ctx = otel_context.set_value(_AZURE_CORE_SUPPRESSED_SPAN_FLAG, True, ctx)
    token = otel_context.attach(ctx)
    try:
        return call()
    finally:
        otel_context.detach(token)


# --- Startup wiring --------------------------------------------------------
def _install_project_instrumentor() -> None:
    """Activate AIProjectInstrumentor GenAI spans + W3C trace-context propagation.

    The Foundry hosted runtime does NOT call ``AIProjectInstrumentor.instrument()``
    for us, so the Projects SDK's GenAI spans + trace-context propagation into
    Foundry OpenAI clients only activate after we call it explicitly. Run before
    any ``get_openai_client()`` acquisition so the httpx hook is registered on
    every client we ever obtain.
    """
    try:
        from opentelemetry.trace import NonRecordingSpan

        if not hasattr(NonRecordingSpan, "attributes"):
            # azure-ai-projects content capture reads this on sampled-out spans.
            NonRecordingSpan.attributes = property(lambda self: {})  # type: ignore[attr-defined]

        from azure.ai.projects.telemetry import AIProjectInstrumentor

        AIProjectInstrumentor().instrument()
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "AIProjectInstrumentor().instrument() failed: %s", e
        )


def _silence_agent_framework_spans() -> None:
    """Silence Agent Framework workflow plumbing spans (workflow.run, message.send).

    Keeps the trace tree focused on user-meaningful GenAI work: router decision,
    model call, specialist dispatch, and remote specialist execution. The no-op
    context manager yields a stub span because Agent Framework reads
    ``attributes`` and calls ``set_attributes`` on the returned span.
    """
    try:
        import contextlib as _contextlib
        import importlib as _importlib
        from agent_framework import observability as _af_observability
        from opentelemetry.trace import INVALID_SPAN_CONTEXT

        hidden_workflow_spans = {"workflow.run", "message.send"}
        original_create_workflow_span = _af_observability.create_workflow_span

        class _SilentFrameworkSpan:
            def __init__(self) -> None:
                self.attributes: dict[str, object] = {}

            def set_attribute(self, key: str, value: object):
                self.attributes[key] = value
                return self

            def set_attributes(self, attributes: dict[str, object]):
                self.attributes.update(attributes)
                return self

            def add_event(self, *_args, **_kwargs):
                return self

            def record_exception(self, *_args, **_kwargs):
                return self

            def set_status(self, *_args, **_kwargs):
                return self

            def is_recording(self) -> bool:
                return False

            def get_span_context(self):
                return INVALID_SPAN_CONTEXT

        @_contextlib.contextmanager
        def _silence_framework_span(*_args, **_kwargs):
            yield _SilentFrameworkSpan()

        def _filtered_workflow_span(name: str, *args, **kwargs):
            if name in hidden_workflow_spans:
                return _silence_framework_span()
            return original_create_workflow_span(name, *args, **kwargs)

        def _patch_framework_span_helpers(module_name: str) -> None:
            try:
                module = _importlib.import_module(module_name)
            except Exception as e:  # pragma: no cover
                logging.getLogger(__name__).debug(
                    "Agent Framework span module %s unavailable: %s", module_name, e
                )
                return

            if hasattr(module, "create_workflow_span"):
                module.create_workflow_span = _filtered_workflow_span
            if hasattr(module, "create_processing_span"):
                module.create_processing_span = _silence_framework_span
            if hasattr(module, "create_edge_group_processing_span"):
                module.create_edge_group_processing_span = _silence_framework_span

        _af_observability.create_workflow_span = _filtered_workflow_span
        _af_observability.create_processing_span = _silence_framework_span
        _af_observability.create_edge_group_processing_span = _silence_framework_span
        for module_name in (
            "agent_framework._workflows._workflow",
            "agent_framework._workflows._functional",
            "agent_framework._workflows._workflow_builder",
            "agent_framework._workflows._workflow_context",
            "agent_framework._workflows._executor",
            "agent_framework._workflows._edge_runner",
        ):
            _patch_framework_span_helpers(module_name)
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "Failed to silence Agent Framework plumbing spans: %s", e
        )


def setup_observability(service_name: str, *, agent_framework: bool = False) -> None:
    """Install all tracing customizations. Call once at startup before serving.

    Args:
        service_name: Names the synthetic root ``invoke_agent <service>`` span and
            the tracer.
        agent_framework: When True, also enables Agent Framework instrumentation
            and silences its workflow plumbing spans.
    """
    configure(service_name)
    _install_project_instrumentor()
    if agent_framework:
        _silence_agent_framework_spans()

        from agent_framework.observability import enable_instrumentation

        enable_instrumentation(enable_sensitive_data=True)
    _install_noise_filter_sampler()
    _install_root_span_registry()

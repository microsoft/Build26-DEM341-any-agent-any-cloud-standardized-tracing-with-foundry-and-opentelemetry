"""OTel bootstrap for the Bengaluru agent.

`microsoft-opentelemetry` is the primary integration here. It configures the
OpenTelemetry SDK plus Azure Monitor export in one call.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource

_configured = False
_noise_filter_sampler_installed = False


def _append_otel_excluded_urls(*patterns: str) -> None:
    value = ",".join(patterns)
    for key in (
        "OTEL_PYTHON_EXCLUDED_URLS",
        "OTEL_PYTHON_REQUESTS_EXCLUDED_URLS",
        "OTEL_PYTHON_URLLIB_EXCLUDED_URLS",
        "OTEL_PYTHON_URLLIB3_EXCLUDED_URLS",
        "OTEL_PYTHON_HTTPX_EXCLUDED_URLS",
        "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS",
    ):
        current = os.environ.get(key)
        os.environ[key] = f"{current},{value}" if current else value


def _enable_genai_content_capture() -> None:
    os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
    os.environ.setdefault(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
        "SPAN_AND_EVENT",
    )
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    _append_otel_excluded_urls(
        r".*/computeMetadata/.*",
        r".*metadata\.google\.internal.*",
        r".*/plan/?$",
        r".*/plan/stream/?$",
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


def _is_noisy_demo_wrapper(name: str, attributes: object | None) -> bool:
    text = _span_match_text(name, attributes)
    route = attributes.get("http.route") if isinstance(attributes, dict) else None
    if name in {"POST /plan", "POST /plan/stream"} or (
        name.startswith("POST ") and route in {"/plan", "/plan/stream"}
    ):
        return True
    if any(
        marker in text
        for marker in (
            "metadata.google.internal",
            "computeMetadata",
        )
    ):
        return True
    if name == "invocation" and isinstance(attributes, dict):
        return bool(attributes.get("microsoft.gen_ai.main_agent.name"))
    return False


def _install_noise_filter_sampler() -> None:
    global _noise_filter_sampler_installed
    if _noise_filter_sampler_installed:
        return

    try:
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
        _noise_filter_sampler_installed = True
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
            if _is_noisy_demo_wrapper(str(name), attributes):
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
    _noise_filter_sampler_installed = True


_agent_span_processors_installed = False


def _install_agent_span_processors(
    agent_invoke_span_name: str, agent_name: str, agent_id: str
) -> None:
    """Ensure the canonical `invoke_agent <agent>` span carries gen_ai I/O.

    The Microsoft distro's ADK instrumentation emits one
    `invoke_agent bengaluru_specialist` span, but the actual
    `gen_ai.input.messages` / `gen_ai.output.messages` land on the deeper
    `generate_content` model span. This processor copies those messages from the
    model span onto the still-live agent span (model span ends before the agent
    span), so the single agent span is self-contained for evaluation.
    """
    global _agent_span_processors_installed
    if _agent_span_processors_installed:
        return

    try:
        import threading

        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning("Span processors unavailable: %s", e)
        return

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return

    class _AgentIOEnrichProcessor(SpanProcessor):
        _MSG_KEYS = ("gen_ai.input.messages", "gen_ai.output.messages")

        def __init__(
            self, invoke_span_name: str, agent_name: str, agent_id: str
        ) -> None:
            self._invoke_span_name = invoke_span_name
            self._agent_name = agent_name
            self._agent_id = agent_id
            self._live: dict[int, object] = {}
            self._lock = threading.Lock()

        def _stamp_agent_identity(self, span) -> None:
            attrs = span.attributes or {}
            if not attrs.get("gen_ai.agent.name"):
                span.set_attribute("gen_ai.agent.name", self._agent_name)
            if not attrs.get("gen_ai.agent.id"):
                span.set_attribute("gen_ai.agent.id", self._agent_id)

        def on_start(self, span, parent_context=None):
            self._stamp_agent_identity(span)
            if span.name == self._invoke_span_name:
                trace_id = span.get_span_context().trace_id
                with self._lock:
                    if trace_id in self._live:
                        span.update_name("adk.pipeline")
                    else:
                        self._live[trace_id] = span

        def on_end(self, span):
            name = span.name
            trace_id = span.context.trace_id
            if name.startswith("generate_content"):
                attrs = span.attributes or {}
                with self._lock:
                    parent = self._live.get(trace_id)
                if parent is None or not parent.is_recording():
                    return
                parent_attrs = parent.attributes or {}
                for key in self._MSG_KEYS:
                    value = attrs.get(key)
                    if value is not None and key not in parent_attrs:
                        parent.set_attribute(key, value)
            elif name == self._invoke_span_name:
                with self._lock:
                    if self._live.get(trace_id) is span:
                        self._live.pop(trace_id, None)

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis: int = 30000):
            return True

    provider.add_span_processor(
        _AgentIOEnrichProcessor(agent_invoke_span_name, agent_name, agent_id)
    )
    _agent_span_processors_installed = True


def configure_telemetry(
    service_name: str,
    cloud_provider: str,
    cloud_region: str,
    agent_name: str,
    demo_city: Optional[str] = None,
) -> trace.Tracer:
    global _configured
    if _configured:
        return trace.get_tracer(service_name)

    _enable_genai_content_capture()

    agent_id = os.getenv("BENGALURU_AGENT_ID", "bengaluru-specialist-gcp")
    attrs: dict[str, str] = {
        "service.namespace": "anyagent-demo",
        "service.name": service_name,
        "cloud.provider": cloud_provider,
        "cloud.region": cloud_region,
        "gen_ai.agent.id": agent_id,
        "gen_ai.agent.name": agent_name,
    }
    if demo_city:
        attrs["demo.city"] = demo_city
    resource = Resource.create(attrs)

    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    enable_sensitive = (
        os.getenv("ENABLE_SENSITIVE_DATA", "true").lower() == "true"
    )

    try:
        from microsoft.opentelemetry import use_microsoft_opentelemetry

        use_microsoft_opentelemetry(
            enable_azure_monitor=bool(conn),
            sampling_ratio=1.0,
            azure_monitor_connection_string=conn,
            enable_sensitive_data=enable_sensitive,
            resource=resource,
        )
        logging.getLogger(__name__).info(
            "microsoft-opentelemetry configured for %s", service_name
        )
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "microsoft-opentelemetry init failed: %s", e
        )

    _install_noise_filter_sampler()
    _install_agent_span_processors(f"invoke_agent {agent_name}", agent_name, agent_id)

    # Auto-propagate W3C trace context into Foundry OpenAI SDK calls.
    if os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
        try:
            from azure.ai.projects.telemetry import AIProjectInstrumentor

            AIProjectInstrumentor().instrument(
                enable_trace_context_propagation=True
            )
        except Exception as e:  # pragma: no cover
            logging.getLogger(__name__).warning(
                "AIProjectInstrumentor instrument failed: %s", e
            )

    _configured = True
    return trace.get_tracer(service_name)


def flush_telemetry(timeout_millis: int = 5000) -> None:
    """Force-flush pending spans and logs before process shutdown."""
    try:
        provider = trace.get_tracer_provider()
        flush = getattr(provider, "force_flush", None)
        if callable(flush):
            flush(timeout_millis)
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning("flush_telemetry failed: %s", e)
    try:
        from opentelemetry._logs import get_logger_provider

        lp = get_logger_provider()
        flush = getattr(lp, "force_flush", None)
        if callable(flush):
            flush(timeout_millis)
    except Exception:  # pragma: no cover
        pass


def current_trace_id_hex() -> str:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx or not ctx.trace_id:
        return ""
    return format(ctx.trace_id, "032x")

"""OpenTelemetry bootstrap for the deepagents Foundry-hosted orchestrator.

Kept separate from ``main.py`` so the orchestrator module stays focused on
routing. Two responsibilities live here:

1. **Import-time GenAI tracing env defaults.** These must be set before
   LangChain / deepagents / the Azure SDKs are imported, so ``main.py`` imports
   this module first.
2. **Startup wiring** via :func:`setup_tracing`.

Integration note (verified against the installed packages): the
``azure-ai-agentserver`` runtime already bootstraps the Microsoft OpenTelemetry
distro itself — ``ResponsesAgentServerHost(...)`` calls
``use_microsoft_opentelemetry`` with the Foundry enrichment span processors,
Azure Monitor export and (in hosted environments) the Agent365 exporter, and
owns the global ``TracerProvider``. It does **not**, however, enable LangChain
instrumentation. So we must NOT call ``use_microsoft_opentelemetry`` ourselves
(that would create a second provider and lose the runtime's enrichment/export to
an "Overriding of current TracerProvider is not allowed" no-op). Instead we
enable only the distro's :class:`LangChainInstrumentor`, which attaches to the
already-registered global provider via ``trace.get_tracer`` and emits the
correct GenAI ``invoke_agent`` / ``execute_tool`` spans for the deep agent.

For this reason :func:`setup_tracing` MUST be called *after* the host
(``ResponsesAgentServerHost``) is constructed so the runtime's provider is in
place. If the distro's LangChain instrumentation is unavailable, it falls back
to ``langchain-azure-ai``'s ``AzureAIOpenTelemetryTracer`` callback handler (as
the Foundry langgraph-chat sample uses) and returns it for ``main.py`` to attach
per-invocation.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
os.environ.setdefault(
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT"
)
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")


def _enable_sensitive_data() -> bool:
    return os.getenv("ENABLE_SENSITIVE_DATA", "true").lower() in ("1", "true", "yes")


def _instrument_langchain(service_name: str) -> bool:
    """Enable the Microsoft distro's LangChain instrumentation. Returns success.

    Attaches to the global (runtime-owned) TracerProvider without re-registering
    one, so the deep agent's spans nest under the host's root and inherit its
    Foundry enrichment + Azure Monitor / A365 export.
    """
    from microsoft.opentelemetry._genai._langchain import LangChainInstrumentor

    instrumentor = LangChainInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        return True
    instrumentor.instrument(agent_name=service_name)
    return True


def _setup_langchain_azure_fallback(service_name: str) -> list:
    """Fall back to a langchain-azure-ai callback tracer. Returns callbacks."""
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer

    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    tracer = AzureAIOpenTelemetryTracer(
        connection_string=conn,
        enable_content_recording=_enable_sensitive_data(),
        name=service_name,
        # The runtime owns the provider/exporter; only auto-configure Azure
        # Monitor when a connection string is explicitly supplied (e.g. local).
        auto_configure_azure_monitor=bool(conn),
    )
    return [tracer]


_agent_span_processors_installed = False


def _install_agent_span_processors(service_name: str) -> None:
    """Collapse the duplicate ``invoke_agent`` span so the orchestrator emits one.

    The Microsoft distro's LangChain instrumentation emits a second
    ``invoke_agent LangGraph`` span nested directly under the canonical
    ``invoke_agent deepagents-orchestrator`` span. Rename it (never drop, so its
    child ``model`` / ``tools`` spans keep their parent) so only one
    ``invoke_agent`` span remains for the orchestrator.
    """
    global _agent_span_processors_installed
    if _agent_span_processors_installed:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    except Exception as exc:  # pragma: no cover - SDK unavailable
        logger.warning("Span processors unavailable: %s", exc)
        return

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return

    class _RenameDuplicateAgentSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None):
            if span.name == "invoke_agent LangGraph" or (
                span.name == f"invoke_agent {service_name}"
                and not span.attributes.get("demo.manual_root")
            ):
                span.update_name("langgraph.pipeline")

        def on_end(self, span):
            pass

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis: int = 30000):
            return True

    provider.add_span_processor(_RenameDuplicateAgentSpanProcessor())
    _agent_span_processors_installed = True


def setup_tracing(service_name: str) -> list:
    """Enable GenAI tracing for the deep agent. Call AFTER constructing the host.

    Returns a list of LangChain callback handlers ``main.py`` must attach to
    every agent invocation. The list is empty when the Microsoft distro's
    LangChain instrumentation is active (it patches the callback manager
    globally, so no per-call callbacks are needed).
    """
    try:
        if _instrument_langchain(service_name):
            _install_agent_span_processors(service_name)
            _log_tracing_diagnostics()
            logger.info(
                "Tracing: microsoft-opentelemetry LangChainInstrumentor active "
                "(attached to runtime TracerProvider)"
            )
            return []
    except Exception as exc:  # pragma: no cover - distro/instrumentation failure
        logger.warning(
            "LangChainInstrumentor failed (%s); falling back to "
            "langchain-azure-ai tracer",
            exc,
        )

    try:
        callbacks = _setup_langchain_azure_fallback(service_name)
        _log_tracing_diagnostics()
        logger.info("Tracing: langchain-azure-ai AzureAIOpenTelemetryTracer active")
        return callbacks
    except Exception as exc:  # pragma: no cover
        logger.warning("All tracing bootstraps failed (%s); running untraced", exc)
        return []


def _log_tracing_diagnostics() -> None:
    """Log non-secret tracing wiring facts to aid hosted-env troubleshooting."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        logger.info(
            "Tracing diagnostics: provider=%s app_insights_conn=%s otlp_endpoint=%s",
            type(provider).__name__,
            "set" if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") else "unset",
            "set" if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") else "unset",
        )
    except Exception:  # pragma: no cover - diagnostics must never be fatal
        pass

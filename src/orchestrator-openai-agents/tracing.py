"""OpenTelemetry bootstrap for the openai-agents Foundry orchestrator.

Kept separate from ``main.py`` so the entrypoint stays focused on wiring the
agent + responses host. Two responsibilities live here:

1. **Import-time GenAI tracing env defaults.** These must be set before the
   ``agents`` SDK and the Azure SDKs are imported, so ``main.py`` imports this
   module first.
2. **Startup wiring** via :func:`setup_tracing`.

Integration note (verified against the installed packages): the
``azure-ai-agentserver`` runtime already bootstraps the Microsoft OpenTelemetry
distro itself — ``ResponsesAgentServerHost(...)`` calls
``use_microsoft_opentelemetry`` with the Foundry enrichment span processors,
Azure Monitor export and (in hosted environments) the Agent365 exporter, and
owns the global ``TracerProvider``. Recent runtimes also install openai-agents trace processors. So we must NOT
call ``use_microsoft_opentelemetry`` ourselves, and we must ensure the
openai-agents SDK has exactly one Azure-Monitor-facing semantic bridge
registered: multiple processors emit duplicate ``invoke_agent`` /
``execute_tool`` spans for the same SDK trace.

For this reason :func:`setup_tracing` MUST be called *after* the host
(``ResponsesAgentServerHost``) is constructed so the runtime's provider is in
place.

Why we do NOT call ``agents.set_tracing_disabled(True)``: the instrumentor
bridges the openai-agents SDK's native tracing into OpenTelemetry via a
``TracingProcessor``. Disabling SDK tracing makes the provider hand out no-op
spans, which starves that bridge and produces zero OTel spans. Instead we keep SDK tracing enabled and drop only the default OpenAI-platform
exporter processor (``BatchTraceProcessor`` -> platform.openai.com), so nothing
leaves for the OpenAI platform while OTel spans keep flowing.
"""
from __future__ import annotations

import logging
import os

# GenAI tracing env defaults — must be set before the agents / Azure SDKs load.
os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
os.environ.setdefault(
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "SPAN_AND_EVENT"
)
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")

_logger = logging.getLogger(__name__)

_TRACING_INSTALLED = False


def _prune_openai_agents_processors() -> bool:
    """Keep exactly one semconv bridge processor for Azure Monitor traces.

    The agentserver runtime may install both the upstream
    ``GenAISemanticProcessor`` and the distro's A365-specific
    ``OpenAIAgentsTraceProcessor``. In this demo's Azure Monitor / Foundry trace
    screen, both processors export OTel spans for the same native SDK spans. The
    upstream processor is the branch we want to keep: it emits semconv
    ``invoke_agent`` spans with input/output and ``execute_tool`` spans that
    become the visible parents of our specialist invocation spans. The A365
    processor's duplicate branch is useful for A365-only consumers, but in App
    Insights it creates duplicate roots/tools that do not parent the specialist
    spans. Also drop the default ``BatchTraceProcessor`` that uploads to
    platform.openai.com.

    Returns True when a semantic processor is present after pruning.
    """
    try:
        from agents.tracing import get_trace_provider

        provider = get_trace_provider()
        multi = getattr(provider, "_multi_processor", None)
        processors = list(getattr(multi, "_processors", ()) or ())
        kept = []
        semantic_seen = False
        for processor in processors:
            name = type(processor).__name__
            if name in {"BatchTraceProcessor", "OpenAIAgentsTraceProcessor"}:
                continue
            if name == "GenAISemanticProcessor":
                if semantic_seen:
                    continue
                semantic_seen = True
            kept.append(processor)
        if kept != processors:
            provider.set_processors(kept)
        return semantic_seen
    except Exception as exc:  # pragma: no cover - defensive, never fatal
        _logger.warning("Could not prune openai-agents trace processors: %s", exc)
        return False


def setup_tracing() -> None:
    """Instrument openai-agents against the runtime's TracerProvider.

    Call once at startup *after* constructing ``ResponsesAgentServerHost`` so the
    agentserver runtime's global provider (Foundry enrichment + Azure Monitor /
    A365 export) is already registered. We only add the openai-agents
    instrumentation and prune the OpenAI-platform exporter; the runtime owns
    export, so this works both locally and in the hosted environment without
    double-registering a provider or exporter.
    """
    global _TRACING_INSTALLED
    if _TRACING_INSTALLED:
        return

    try:
        from opentelemetry.instrumentation.openai_agents import (
            OpenAIAgentsInstrumentor,
        )

        instrumentor = OpenAIAgentsInstrumentor()
        if not _prune_openai_agents_processors() and (
            not instrumentor.is_instrumented_by_opentelemetry
        ):
            instrumentor.instrument(skip_dep_check=True)
        _prune_openai_agents_processors()
        _log_tracing_diagnostics()
        _logger.info(
            "Tracing: openai-agents GenAI semantic bridge active "
            "(single processor attached to runtime TracerProvider)"
        )
    except Exception as exc:  # pragma: no cover - instrumentation failure
        _logger.warning("openai-agents instrumentation failed (%s); untraced", exc)

    _TRACING_INSTALLED = True


def _log_tracing_diagnostics() -> None:
    """Log non-secret tracing wiring facts to aid hosted-env troubleshooting."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        processor_names = _openai_agents_processor_names()
        _logger.info(
            "Tracing diagnostics: provider=%s openai_agents_processors=%s "
            "app_insights_conn=%s otlp_endpoint=%s",
            type(provider).__name__,
            processor_names,
            "set" if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING") else "unset",
            "set" if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") else "unset",
        )
    except Exception:  # pragma: no cover - diagnostics must never be fatal
        pass


def _openai_agents_processor_names() -> list[str]:
    try:
        from agents.tracing import get_trace_provider

        provider = get_trace_provider()
        multi = getattr(provider, "_multi_processor", None)
        return [type(p).__name__ for p in getattr(multi, "_processors", ()) or ()]
    except Exception:  # pragma: no cover
        return []

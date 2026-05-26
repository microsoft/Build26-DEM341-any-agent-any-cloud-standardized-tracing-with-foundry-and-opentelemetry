"""OTel bootstrap for this agent.

`microsoft-opentelemetry` is the primary integration here. It configures the
OpenTelemetry SDK plus Azure Monitor export in one call.

`azure-monitor-opentelemetry-exporter` is used directly on AWS Lambda so spans
are exported synchronously before the runtime freezes between invocations.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource

_configured = False


def _is_lambda() -> bool:
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _configure_lambda_span_export(conn: str) -> None:
    if not _is_lambda():
        return

    try:
        from azure.monitor.opentelemetry.exporter import (
            AzureMonitorTraceExporter,
        )
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = trace.get_tracer_provider()
        add = getattr(provider, "add_span_processor", None)
        if callable(add):
            add(
                SimpleSpanProcessor(
                    AzureMonitorTraceExporter.from_connection_string(conn)
                )
            )
            logging.getLogger(__name__).info(
                "Added synchronous Azure Monitor exporter for Lambda"
            )
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "Failed to add Lambda span exporter: %s", e
        )


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

    attrs: dict[str, str] = {
        "service.namespace": "anyagent-demo",
        "service.name": service_name,
        "cloud.provider": cloud_provider,
        "cloud.region": cloud_region,
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
            enable_azure_monitor=bool(conn) and not _is_lambda(),
            azure_monitor_connection_string=conn,
            enable_sensitive_data=enable_sensitive,
            resource=resource,
            instrumentation_options={
                "langchain": {
                    "enabled": True,
                    "agent_id": os.getenv(
                        "SEATTLE_AGENT_ID", "seattle-specialist-aws"
                    ),
                    "agent_name": agent_name,
                },
            },
        )
        logging.getLogger(__name__).info(
            "microsoft-opentelemetry configured for %s", service_name
        )
    except Exception as e:  # pragma: no cover
        logging.getLogger(__name__).warning(
            "microsoft-opentelemetry init failed: %s", e
        )

    if conn:
        _configure_lambda_span_export(conn)

    # Auto-propagate W3C trace context into Foundry OpenAI SDK calls.
    if os.getenv("FOUNDRY_PROJECT_ENDPOINT"):
        os.environ.setdefault("AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING", "true")
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
    """Force-flush all pending spans/logs. Critical on AWS Lambda where the
    runtime freezes the process between invocations and the BatchSpanProcessor
    would otherwise drop spans."""
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

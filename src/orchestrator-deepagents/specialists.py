"""Shared remote-specialist call helpers for the alternative orchestrators.

Framework-agnostic async functions that invoke the three cross-cloud travel
specialists and emit a single OTel GenAI ``invoke_agent <name>`` CLIENT span
each, injecting the W3C ``traceparent`` *inside* that span so the remote
server-side spans nest under this invocation:

    * plan_seattle    -> LangGraph specialist on AWS Lambda  (boto3 lambda:Invoke)
    * plan_bengaluru  -> Google ADK specialist on GCP Cloud Run (HTTP /plan)
    * plan_xian       -> Xi'an Foundry Prompt Agent (Responses API)

This module is a self-contained extraction of the proven call logic in
``orchestrator/main.py`` so the deepagents and openai-agents orchestrators can
reuse identical transport + tracing behavior. It deliberately depends only on
opentelemetry + azure SDKs (no agent_framework), so it can be wrapped as a tool
by any framework. Config is read lazily so the module imports cleanly for local
smoke tests even when the specialist env vars are unset.
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import TypeVar

from opentelemetry import context as otel_context, trace
from opentelemetry.propagate import inject
from opentelemetry.trace import SpanKind

_T = TypeVar("_T")

tracer = trace.get_tracer("orchestrator-specialists")

SEATTLE_AGENT_NAME = "seattle_specialist"
BENGALURU_AGENT_NAME = "bengaluru_specialist"
SEATTLE_AGENT_ID = "seattle-specialist-aws"
BENGALURU_AGENT_ID = "bengaluru-specialist-gcp"

try:
    from opentelemetry.context import (
        _SUPPRESS_HTTP_INSTRUMENTATION_KEY,
        _SUPPRESS_INSTRUMENTATION_KEY,
    )
except ImportError:  # pragma: no cover
    _SUPPRESS_HTTP_INSTRUMENTATION_KEY = "suppress_http_instrumentation"
    _SUPPRESS_INSTRUMENTATION_KEY = "suppress_instrumentation"

try:
    from azure.core.tracing.ext import opentelemetry_span as _azure_core_otel_span

    _AZURE_CORE_SUPPRESSED_SPAN_FLAG = getattr(
        _azure_core_otel_span, "_SUPPRESSED_SPAN_FLAG", "SUPPRESSED_SPAN_FLAG"
    )
except Exception:  # pragma: no cover
    _AZURE_CORE_SUPPRESSED_SPAN_FLAG = "SUPPRESSED_SPAN_FLAG"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _genai_messages_json(role: str, content: str) -> str:
    return json.dumps([{"role": role, "parts": [{"type": "text", "content": content}]}])


def _is_recording(span: object) -> bool:
    is_recording = getattr(span, "is_recording", None)
    return bool(callable(is_recording) and is_recording())


def _set_span_input_message(span: object, content: str) -> None:
    if not _is_recording(span):
        return
    span.set_attribute("gen_ai.input.messages", _genai_messages_json("user", content))
    span.set_attribute("demo.input.content", content)


def _set_span_output_message(span: object, content: str) -> None:
    if not _is_recording(span):
        return
    span.set_attribute(
        "gen_ai.output.messages", _genai_messages_json("assistant", content)
    )
    span.set_attribute("demo.output.content", content)


def _stamp_target_agent(span: object, agent_name: str, agent_id: str) -> None:
    if not _is_recording(span):
        return
    span.set_attribute("gen_ai.agent.name", agent_name)
    span.set_attribute("gen_ai.agent.id", agent_id)
    span.set_attribute("gen_ai.target_agent.name", agent_name)
    span.set_attribute("gen_ai.target_agent.id", agent_id)
    span.set_attribute("demo.target_agent_id", agent_id)


def _preserve_demo_trace_headers(headers: dict[str, str]) -> None:
    traceparent = headers.get("traceparent")
    if traceparent:
        headers["x-demo-traceparent"] = traceparent
    tracestate = headers.get("tracestate")
    if tracestate:
        headers["x-demo-tracestate"] = tracestate


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


def _without_azure_core_span_suppression(call: Callable[[], _T]) -> _T:
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


def _run_foundry_call_in_clean_context(span, call: Callable[[], _T]) -> _T:
    """Run the Foundry call under a FRESH OTel context containing only ``span``.

    Concurrent specialist fan-out can leak ``_SUPPRESS_INSTRUMENTATION_KEY`` and
    the azure-core suppressed-span flag into this branch's copied OTel context
    (both are set by the HTTP specialists via
    :func:`_without_http_auto_instrumentation`). If either is still set when the
    Foundry responses instrumentor starts its span, OTel hands back a
    ``NonRecordingSpan`` and the instrumentor crashes while appending messages
    (``'NonRecordingSpan' object has no attribute 'attributes'``).

    Rather than flipping individual keys on the (possibly polluted) current
    context, we build a brand-new context from just the ``invoke_agent`` span
    (mirroring the agent-framework orchestrator's ``extract(trace_context)``
    approach). A fresh context carries no suppression keys, so the instrumented
    Foundry call always records on a live span and nests under ``invoke_agent``.
    Raw httpx CLIENT spans are kept suppressed so only the GenAI
    chat/conversation spans are emitted (no duplicate transport spans).
    """
    from opentelemetry.trace import set_span_in_context

    ctx = set_span_in_context(span)
    ctx = otel_context.set_value(_SUPPRESS_HTTP_INSTRUMENTATION_KEY, True, ctx)
    token = otel_context.attach(ctx)
    try:
        return call()
    finally:
        otel_context.detach(token)


def _post_json_without_auto_instrumentation(
    url: str, headers: dict[str, str], payload: dict[str, object]
) -> dict[str, object]:
    from urllib import error as urllib_error
    from urllib import request as urllib_request

    request = urllib_request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        def _post() -> dict[str, object]:
            with urllib_request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))

        return _without_http_auto_instrumentation(_post)
    except urllib_error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"POST {url} failed with {e.code}: {body}") from e


def _format(data: dict, fallback_city: str) -> str:
    itinerary = data.get("itinerary", "")
    return (
        f"### {data.get('city', fallback_city)} "
        f"(agent: {data.get('agent', '')}, trace_id: {data.get('trace_id', '')})\n\n"
        f"{itinerary}"
    )


async def _call_http_specialist(
    name: str, agent_name: str, agent_id: str, url: str, query: str
) -> str:
    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": agent_id,
            "gen_ai.agent.name": agent_name,
            "demo.target_agent": name,
            "http.url": f"{url}/plan",
        },
    ) as span:
        _stamp_target_agent(span, agent_name, agent_id)
        _set_span_input_message(span, query)
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": _env("DEMO_SHARED_SECRET", "devsecret"),
        }
        inject(headers)
        _preserve_demo_trace_headers(headers)
        data = await asyncio.to_thread(
            _post_json_without_auto_instrumentation,
            f"{url}/plan",
            headers,
            {"query": query},
        )
        _set_span_output_message(span, str(data.get("itinerary", "")))
        return _format(data, name.capitalize())


async def _call_seattle_lambda(query: str) -> str:
    lambda_name = _env("SEATTLE_LAMBDA_NAME")
    region = _env("SEATTLE_LAMBDA_REGION", "us-west-2")
    with tracer.start_as_current_span(
        f"invoke_agent {SEATTLE_AGENT_NAME}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": SEATTLE_AGENT_ID,
            "gen_ai.agent.name": SEATTLE_AGENT_NAME,
            "demo.target_agent": "seattle",
            "aws.lambda.function_name": lambda_name,
            "cloud.provider": "aws",
        },
    ) as span:
        _stamp_target_agent(span, SEATTLE_AGENT_NAME, SEATTLE_AGENT_ID)
        _set_span_input_message(span, query)
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": _env("DEMO_SHARED_SECRET", "devsecret"),
        }
        inject(headers)
        _preserve_demo_trace_headers(headers)
        event = {
            "version": "2.0",
            "rawPath": "/plan",
            "headers": headers,
            "requestContext": {"http": {"method": "POST", "path": "/plan"}},
            "body": json.dumps({"query": query}),
            "isBase64Encoded": False,
        }
        import boto3

        client = boto3.client("lambda", region_name=region)
        resp = await asyncio.to_thread(
            client.invoke,
            FunctionName=lambda_name,
            Payload=json.dumps(event).encode(),
        )
        payload = json.loads(resp["Payload"].read())
        if "errorMessage" in payload:
            raise RuntimeError(payload["errorMessage"])
        data = json.loads(payload.get("body", "{}"))
        _set_span_output_message(span, str(data.get("itinerary", "")))
        return _format(data, "Seattle")


_foundry_openai_client = None


def _get_foundry_openai_client():
    global _foundry_openai_client
    if _foundry_openai_client is None:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential

        project = AIProjectClient(
            endpoint=_env("FOUNDRY_PROJECT_ENDPOINT"),
            credential=DefaultAzureCredential(),
        )
        _foundry_openai_client = project.get_openai_client()
    return _foundry_openai_client


async def _call_xian_foundry(query: str) -> str:
    xian_name = _env("XIAN_AGENT_NAME", "xian-specialist")
    xian_id = _env("XIAN_AGENT_ID", "xian-specialist-azure")
    with tracer.start_as_current_span(
        f"invoke_agent {xian_name}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": xian_id,
            "gen_ai.agent.name": xian_name,
            "demo.target_agent": "xian",
            "cloud.provider": "azure",
        },
    ) as span:
        _stamp_target_agent(span, xian_name, xian_id)
        _set_span_input_message(span, query)
        oa = _get_foundry_openai_client()

        def _run_sync() -> tuple[str, str, str]:
            def _invoke() -> tuple[str, str, str]:
                conv = oa.conversations.create(
                    items=[{"type": "message", "role": "user", "content": query}]
                )
                resp = oa.responses.create(
                    conversation=conv.id,
                    extra_body={
                        "agent_reference": {
                            "name": xian_name,
                            "type": "agent_reference",
                        }
                    },
                    input="Build the plan now.",
                )
                parts: list[str] = []
                for item in getattr(resp, "output", []) or []:
                    if getattr(item, "type", "") == "message":
                        for content in getattr(item, "content", []) or []:
                            t = getattr(content, "text", "") or ""
                            if t:
                                parts.append(t)
                return "".join(parts), conv.id, getattr(resp, "id", "")

            return _run_foundry_call_in_clean_context(span, _invoke)

        text, conv_id, response_id = await asyncio.to_thread(_run_sync)
        if conv_id:
            span.set_attribute("gen_ai.conversation.id", conv_id)
        if response_id:
            span.set_attribute("gen_ai.response.id", response_id)
        _set_span_output_message(span, text)
        return text.strip()


# --- Public framework-agnostic specialist callables -------------------------


async def plan_seattle(query: str) -> str:
    """Plan a trip in Seattle, USA (LangGraph specialist on AWS Lambda)."""
    url = _env("SEATTLE_AGENT_URL")
    if url:
        return await _call_http_specialist(
            "seattle", SEATTLE_AGENT_NAME, SEATTLE_AGENT_ID, url, query
        )
    return await _call_seattle_lambda(query)


async def plan_bengaluru(query: str) -> str:
    """Plan a trip in Bengaluru, India (Google ADK specialist on GCP Cloud Run)."""
    url = _env("BENGALURU_AGENT_URL", "http://localhost:8081")
    return await _call_http_specialist(
        "bengaluru", BENGALURU_AGENT_NAME, BENGALURU_AGENT_ID, url, query
    )


async def plan_xian(query: str) -> str:
    """Plan a trip in Xi'an, China (Azure-hosted specialist).

    When ``XIAN_AGENT_URL`` is set, Xi'an is reached over plain HTTP/A2A like
    Seattle and Bengaluru — the orchestrator owns the single
    ``invoke_agent xian-specialist`` CLIENT span and the remote server runs
    untraced. This replaces the in-process Foundry prompt-agent call
    (``_call_xian_foundry``), whose ``responses.create(agent_reference=...)``
    path crashed azure-ai-projects' instrumentor under concurrent fan-out.
    """
    url = _env("XIAN_AGENT_URL")
    if url:
        xian_name = _env("XIAN_AGENT_NAME", "xian-specialist")
        return await _call_http_specialist(
            "xian", xian_name, _env("XIAN_AGENT_ID", "xian-specialist-azure"), url, query
        )
    return await _call_xian_foundry(query)

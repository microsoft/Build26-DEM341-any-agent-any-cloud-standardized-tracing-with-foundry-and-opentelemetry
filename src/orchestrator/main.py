"""Foundry-hosted travel orchestrator (Microsoft Agent Framework Workflow).

The orchestrator is now a **multi-agent workflow** built with
``agent_framework.WorkflowBuilder``:

    user query
        |
        v
    RouterExecutor (LLM picks which cities are mentioned)
        |
        +-> SeattleExecutor    (AWS Lambda via boto3)
        +-> BengaluruExecutor  (GCP Cloud Run via HTTPS)
        +-> XianExecutor       (Foundry Prompt Agent via Responses API)
        +-> CopilotExecutor    (GitHub Copilot SDK fallback for any other city)
        |
        v
    AggregatorExecutor (combines results into final Markdown)
        |
        v
    workflow output

The whole workflow is wrapped via ``.as_agent()`` and hosted with
``ResponsesHostServer`` from ``agent-framework-foundry-hosting``, exposing the
OpenAI Responses-compatible REST endpoint on port 8088.

Foundry Observability captures the unified W3C trace across all four clouds.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from dataclasses import dataclass

# Import observability first: it applies the GenAI tracing env defaults that must
# be set before agent_framework / the Azure SDKs are imported, and provides the
# span/trace helpers extracted from this module.
import observability
from observability import (
    _add_root_choice,
    _add_root_user_message,
    _current_root_agent_span,
    _current_trace_id_hex,
    _is_recording,
    _preserve_demo_trace_headers,
    _set_span_input_message,
    _set_span_output_message,
    _start_demo_root_agent_span,
    _without_azure_core_span_suppression,
    _without_http_auto_instrumentation,
)

from agent_framework import (
    Agent,
    Executor,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from agent_framework_foundry_hosting import _responses as _foundry_responses
from azure.identity import DefaultAzureCredential
from opentelemetry import context as otel_context, trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind
from typing_extensions import Never

SERVICE_NAME = "foundry-orchestrator"
AGENT_NAME = "orchestrator"
REGION = os.getenv("AZURE_REGION", "eastus2")

# Stable agent identifiers for the four specialists. These must match the
# `gen_ai.agent.name` each specialist emits on its own server-side spans so
# that client-side `invoke_agent <name>` spans correlate cleanly with the
# server-side agent execution in Foundry Observability.
SEATTLE_AGENT_NAME = "seattle_specialist"
BENGALURU_AGENT_NAME = "bengaluru_specialist"
SEATTLE_AGENT_ID = "seattle-specialist-aws"
BENGALURU_AGENT_ID = "bengaluru-specialist-gcp"
COPILOT_AGENT_NAME = "copilot-fallback"

# OTel bootstrap (TracerProvider, Azure Monitor exporter, instrumentors,
# resource attributes) is configured by the Foundry hosted-agent runtime;
# we just grab a tracer from the global provider.
tracer = trace.get_tracer(SERVICE_NAME)


# --- Specialist endpoints --------------------------------------------------
SEATTLE_LAMBDA_NAME = os.environ["SEATTLE_LAMBDA_NAME"]
SEATTLE_LAMBDA_REGION = os.getenv("SEATTLE_LAMBDA_REGION", "us-west-2")
SEATTLE_URL = os.getenv("SEATTLE_AGENT_URL", "")
BENGALURU_URL = os.getenv(
    "BENGALURU_AGENT_URL",
    "http://localhost:8081",
)
XIAN_AGENT_NAME = os.environ["XIAN_AGENT_NAME"]  # remote Foundry Prompt Agent
XIAN_URL = os.getenv("XIAN_AGENT_URL", "")  # Azure-hosted Xi'an A2A/HTTP server
FOUNDRY_PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "devsecret")


ROUTER_INSTRUCTIONS = """You are a city router for a multi-agent travel concierge.

Given a customer's travel question, pick ALL specialists needed to answer it.
You have FOUR specialists:

  * "seattle"  - Seattle, USA specialist (LangGraph on AWS Lambda)
  * "bengaluru" - Bengaluru, India specialist (Google ADK on GCP)
  * "xian"     - Xi'an, China specialist (Foundry Prompt Agent)
  * "copilot"  - General-purpose travel knowledge fallback (GitHub Copilot)

Rules:
- If the customer asks about Seattle, include "seattle".
- If the customer asks about Bengaluru, include "bengaluru".
- If the customer asks about Xi'an, include "xian".
- If the customer mentions multiple supported cities, include ALL of them
  in the same order they appear in the message.
- If the customer asks about an unsupported city, or no supported city is
  mentioned, return only "copilot".
- Do not include "copilot" when any supported city is selected.
- Never invent extra city keys. Stick to the list.

Respond with ONLY a JSON object, no markdown fences, no commentary:

{"cities": ["seattle", "bengaluru"]}
"""


# --- Tool helpers ----------------------------------------------------------

async def _call_http_specialist(
    name: str, agent_name: str, agent_id: str, url: str, query: str
) -> str:
    """Call a FastAPI specialist using its non-streaming `/plan` endpoint.

    Emits an OTel GenAI ``invoke_agent <agent_name>`` span and injects the
    W3C traceparent under that span so the specialist's server-side spans
    nest under this invocation (not under whatever was current upstream).
    """
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
        _set_span_input_message(span, query)
        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "multi_cloud_agent",
                "gen_ai.message.content": query[:4000],
            },
        )
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": SHARED_SECRET,
        }
        # Inject traceparent INSIDE the invoke_agent span so the remote
        # server-side spans are parented under this invocation.
        inject(headers)
        _preserve_demo_trace_headers(headers)
        data = await asyncio.to_thread(
            _post_json_without_auto_instrumentation,
            f"{url}/plan",
            headers,
            {"query": query},
        )
        itinerary = data.get("itinerary", "")
        _set_span_output_message(span, str(itinerary))
        if itinerary:
            span.add_event(
                "gen_ai.choice",
                attributes={
                    "gen_ai.system": "multi_cloud_agent",
                    "gen_ai.message.content": str(itinerary)[:4000],
                },
            )
        return (
            f"### {data.get('city', name)} "
            f"(agent: {data.get('agent', '')}, trace_id: {data.get('trace_id', '')})\n\n"
            f"{itinerary}"
        )


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


async def _call_seattle_lambda(query: str) -> str:
    """Invoke the Seattle agent on AWS Lambda via the non-streaming `/plan`
    endpoint using a synchronous `lambda:Invoke`.

    Emits an OTel GenAI ``invoke_agent seattle_specialist`` span and injects
    the W3C traceparent under that span so the Seattle agent's server-side
    spans nest under this invocation (not under whatever was current upstream).
    """
    with tracer.start_as_current_span(
        f"invoke_agent {SEATTLE_AGENT_NAME}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": SEATTLE_AGENT_NAME,
            "demo.target_agent": "seattle",
            "aws.lambda.function_name": SEATTLE_LAMBDA_NAME,
            "cloud.provider": "aws",
        },
    ) as span:
        _set_span_input_message(span, query)
        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "multi_cloud_agent",
                "gen_ai.message.content": query[:4000],
            },
        )
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": SHARED_SECRET,
        }
        # Inject traceparent INSIDE the invoke_agent span so the remote
        # Lambda server-side spans are parented under this invocation.
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

        client = boto3.client("lambda", region_name=SEATTLE_LAMBDA_REGION)
        resp = await asyncio.to_thread(
            client.invoke,
            FunctionName=SEATTLE_LAMBDA_NAME,
            Payload=json.dumps(event).encode(),
        )
        raw = resp["Payload"].read()
        payload = json.loads(raw)
        if "errorMessage" in payload:
            raise RuntimeError(payload["errorMessage"])
        data = json.loads(payload.get("body", "{}"))
        itinerary = data.get("itinerary", "")
        _set_span_output_message(span, str(itinerary))
        if itinerary:
            span.add_event(
                "gen_ai.choice",
                attributes={
                    "gen_ai.system": "multi_cloud_agent",
                    "gen_ai.message.content": str(itinerary)[:4000],
                },
            )
        return (
            f"### {data.get('city', 'Seattle')} "
            f"(agent: {data.get('agent', '')}, trace_id: {data.get('trace_id', '')})\n\n"
            f"{itinerary}"
        )


_foundry_openai_client = None


def _get_foundry_openai_client():
    global _foundry_openai_client
    if _foundry_openai_client is None:
        from azure.ai.projects import AIProjectClient

        project = AIProjectClient(
            endpoint=FOUNDRY_PROJECT_ENDPOINT,
            credential=DefaultAzureCredential(),
        )
        _foundry_openai_client = project.get_openai_client()
    return _foundry_openai_client


async def _call_xian_foundry(query: str) -> str:
    """Invoke Xi'an as a Foundry Prompt Agent v2 (non-streaming).

    Emits one visible ``invoke_agent <xian-agent-name>`` CLIENT span, parented
    under the current workflow/router span just like the Seattle and Bengaluru
    boundaries. This gives Xi'an a single, content-rich boundary span instead of
    scattering its input/output onto the router span.

    The Foundry prompt-agent runtime emits its own server-side span subtree
    (``invoke_agent xian-specialist:<ver>`` -> model/tool spans) under an
    internal parent span that the runtime does NOT export to App Insights, so
    that subtree renders orphaned. We cannot repair that edge from the client,
    but we stamp ``gen_ai.response.id`` / ``gen_ai.conversation.id`` here so the
    orphaned runtime subtree (which carries the same ids) stays correlatable to
    this visible, correctly-parented boundary span.
    """
    with tracer.start_as_current_span(
        f"invoke_agent {XIAN_AGENT_NAME}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": XIAN_AGENT_NAME,
            "demo.target_agent": "xian",
            "cloud.provider": "azure",
        },
    ) as span:
        _set_span_input_message(span, query)
        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "az.ai.foundry",
                "gen_ai.message.content": query,
            },
        )
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
                            "name": XIAN_AGENT_NAME,
                            "type": "agent_reference",
                        }
                    },
                    input="Build the plan now.",
                )
                text_parts: list[str] = []
                for item in getattr(resp, "output", []) or []:
                    if getattr(item, "type", "") == "message":
                        for content in getattr(item, "content", []) or []:
                            t = getattr(content, "text", "") or ""
                            if t:
                                text_parts.append(t)
                return "".join(text_parts), conv.id, getattr(resp, "id", "")

            return _without_azure_core_span_suppression(_invoke)

        text, conv_id, response_id = await asyncio.to_thread(_run_sync)
        if conv_id:
            span.set_attribute("gen_ai.conversation.id", conv_id)
        if response_id:
            span.set_attribute("gen_ai.response.id", response_id)
        _set_span_output_message(span, text)
        if text:
            span.add_event(
                "gen_ai.choice",
                attributes={
                    "gen_ai.system": "az.ai.foundry",
                    "gen_ai.message.content": text,
                },
            )
        return text.strip()


# --- Tools exposed to the orchestrator agent --------------------------------

# --- Specialist callable wrappers ------------------------------------------
# These are thin async functions that just dispatch to the right transport.
# They are called by the workflow's specialist executors, not by an LLM.

async def _specialist_seattle(query: str) -> str:
    """Plan a trip in Seattle, USA via the LangGraph specialist on AWS Lambda."""
    if SEATTLE_URL:
        return await _call_http_specialist(
            "seattle", SEATTLE_AGENT_NAME, SEATTLE_AGENT_ID, SEATTLE_URL, query
        )
    return await _call_seattle_lambda(query)


async def _specialist_bengaluru(query: str) -> str:
    """Plan a trip in Bengaluru, India via the ADK specialist on GCP."""
    return await _call_http_specialist(
        "bengaluru", BENGALURU_AGENT_NAME, BENGALURU_AGENT_ID, BENGALURU_URL, query
    )


async def _specialist_xian(query: str) -> str:
    """Plan a trip in Xi'an, China.

    When ``XIAN_AGENT_URL`` is set, Xi'an is reached over plain HTTP/A2A like
    Seattle and Bengaluru (orchestrator owns the single ``invoke_agent`` CLIENT
    span; remote server runs untraced). Falls back to the in-process Foundry
    prompt-agent call otherwise.
    """
    if XIAN_URL:
        return await _call_http_specialist(
            "xian",
            XIAN_AGENT_NAME,
            os.getenv("XIAN_AGENT_ID", "xian-specialist-azure"),
            XIAN_URL,
            query,
        )
    return await _call_xian_foundry(query)


# --- Copilot SDK fallback for any other city -------------------------------
_copilot_client = None
_copilot_lock = asyncio.Lock()
COPILOT_MODEL = os.getenv("COPILOT_MODEL", "claude-sonnet-4.5")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
COPILOT_PROVIDER_MODEL = os.getenv(
    "COPILOT_PROVIDER_MODEL",
    os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4"),
).strip()
COPILOT_PROVIDER_API_VERSION = os.getenv("COPILOT_PROVIDER_API_VERSION", "preview").strip()
FOUNDRY_AGENT_TOOLBOX_ENDPOINT = os.getenv(
    "COPILOT_TOOLBOX_ENDPOINT",
    os.getenv("FOUNDRY_AGENT_TOOLBOX_ENDPOINT", os.getenv("FOUNDRY_TOOLBOX_ENDPOINT", "")),
).strip()
FOUNDRY_AGENT_TOOLBOX_FEATURES = os.getenv(
    "COPILOT_TOOLBOX_FEATURES",
    os.getenv("FOUNDRY_AGENT_TOOLBOX_FEATURES", "Toolboxes=V1Preview"),
).strip()
_toolbox_credential = None
_azure_openai_credential = None


async def _get_copilot_client():
    """Lazily start a single CopilotClient for the process lifetime."""
    global _copilot_client
    if _copilot_client is not None:
        return _copilot_client
    async with _copilot_lock:
        if _copilot_client is not None:
            return _copilot_client
        from copilot import CopilotClient, SubprocessConfig

        # Route the Copilot CLI's OTLP traces through the sidecar OTel Collector,
        # which forwards them to Application Insights via the azuremonitor exporter.
        # The CLI auto-propagates W3C traceparent, so its spans stitch under the
        # orchestrator's "chat github-copilot" span.
        telemetry_cfg = {
            "otlp_endpoint": os.getenv("COPILOT_OTLP_ENDPOINT", "http://127.0.0.1:4318"),
            "exporter_type": "otlp-http",
            "source_name": "github-copilot",
            "capture_content": True,
        }
        if GITHUB_TOKEN:
            config = SubprocessConfig(
                github_token=GITHUB_TOKEN,
                telemetry=telemetry_cfg,
            )
        elif AZURE_OPENAI_ENDPOINT:
            config = SubprocessConfig(
                use_logged_in_user=False,
                telemetry=telemetry_cfg,
            )
        else:
            config = SubprocessConfig(telemetry=telemetry_cfg)
        client = CopilotClient(config)
        await client.start()
        _copilot_client = client
        return client


def _get_toolbox_token() -> str:
    global _toolbox_credential
    if _toolbox_credential is None:
        _toolbox_credential = DefaultAzureCredential()
    return _without_http_auto_instrumentation(
        lambda: _toolbox_credential.get_token("https://ai.azure.com/.default").token
    )


def _get_azure_openai_token() -> str:
    global _azure_openai_credential
    if _azure_openai_credential is None:
        _azure_openai_credential = DefaultAzureCredential()
    return _without_http_auto_instrumentation(
        lambda: _azure_openai_credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        ).token
    )


async def _copilot_provider_config() -> tuple[str, dict[str, object] | None]:
    if GITHUB_TOKEN or not AZURE_OPENAI_ENDPOINT:
        return COPILOT_MODEL, None

    token = await asyncio.to_thread(_get_azure_openai_token)
    base_url = AZURE_OPENAI_ENDPOINT
    if not base_url.endswith("/openai/v1"):
        base_url = f"{base_url}/openai/v1"

    return COPILOT_PROVIDER_MODEL, {
        "type": "azure",
        "wire_api": "responses",
        "base_url": base_url,
        "bearer_token": token,
        "azure": {"api_version": COPILOT_PROVIDER_API_VERSION},
    }


async def _copilot_toolbox_mcp_servers() -> dict[str, object]:
    if not FOUNDRY_AGENT_TOOLBOX_ENDPOINT:
        return {}

    token = await asyncio.to_thread(_get_toolbox_token)
    headers = {"Authorization": f"Bearer {token}"}
    if FOUNDRY_AGENT_TOOLBOX_FEATURES:
        headers["Foundry-Features"] = FOUNDRY_AGENT_TOOLBOX_FEATURES

    return {
        "foundry_toolbox": {
            "type": "http",
            "url": FOUNDRY_AGENT_TOOLBOX_ENDPOINT,
            "tools": ["*"],
            "timeout": 120000,
            "headers": headers,
        }
    }


async def _specialist_copilot(query: str) -> str:
    """General-knowledge travel fallback powered by GitHub Copilot."""
    import inspect

    from copilot.generated.session_events import (
        AssistantMessageData,
        AssistantMessageDeltaData,
        AssistantUsageData,
        ExternalToolRequestedData,
        McpOauthRequiredData,
        PermissionRequestedData,
        SessionErrorData,
        SessionMcpServersLoadedData,
        SessionMcpServerStatusChangedData,
        SessionWarningData,
        ToolExecutionCompleteData,
        ToolExecutionStartData,
        UserInputRequestedData,
    )
    from copilot.session import PermissionHandler

    with tracer.start_as_current_span(
        f"invoke_agent {COPILOT_AGENT_NAME}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.system": "github-copilot",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.request.model": COPILOT_MODEL,
            "gen_ai.agent.name": COPILOT_AGENT_NAME,
            "demo.target_agent": "copilot",
            "cloud.provider": "github",
        },
    ) as span:
        prompt = (
            "You are a concise travel concierge. Plan or answer the following "
            "travel question in under 250 words. Use clean Markdown with a short "
            "intro and bullet points. Use the available web_search tool when the "
            "question depends on current local information, events, hours, or "
            "fresh facts; cite useful URLs from tool results when available.\n\n"
            f"Customer: {query}"
        )
        _set_span_input_message(span, query)

        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "github-copilot",
                "gen_ai.message.content": query[:4000],
            },
        )

        assistant_deltas: list[str] = []
        assistant_final: list[str] = []
        session_errors: list[str] = []
        usage_tokens = {"input": 0, "output": 0}
        response_model: list[str] = []
        tool_calls: list[str] = []

        def _event_text(value: object, limit: int = 1000) -> str:
            return str(value or "")[:limit]

        def _noninteractive_user_input(request, _invocation):  # noqa: ANN001
            choices = request.get("choices") or []
            answer = choices[0] if choices else "Continue with the best available information."
            return {"answer": answer, "wasFreeform": not bool(choices)}

        def on_event(event):
            data = event.data
            if isinstance(data, AssistantMessageData):
                if data.content:
                    assistant_final.append(data.content)
            elif isinstance(data, AssistantMessageDeltaData):
                if data.delta_content:
                    assistant_deltas.append(data.delta_content)
            elif isinstance(data, SessionErrorData):
                session_errors.append(data.message)
                span.add_event(
                    "copilot.session.error",
                    attributes={
                        "error.type": data.error_type,
                        "error.message": _event_text(data.message),
                        "http.response.status_code": data.status_code or 0,
                        "url.full": data.url or "",
                    },
                )
            elif isinstance(data, SessionWarningData):
                span.add_event(
                    "copilot.session.warning",
                    attributes={
                        "warning.type": data.warning_type,
                        "warning.message": _event_text(data.message),
                        "url.full": data.url or "",
                    },
                )
            elif isinstance(data, SessionMcpServersLoadedData):
                for server in data.servers:
                    span.add_event(
                        "mcp.server.loaded",
                        attributes={
                            "mcp.server.name": server.name,
                            "mcp.server.status": getattr(server.status, "value", str(server.status)),
                            "error.message": _event_text(server.error),
                            "mcp.server.source": server.source or "",
                        },
                    )
            elif isinstance(data, SessionMcpServerStatusChangedData):
                span.add_event(
                    "mcp.server.status",
                    attributes={
                        "mcp.server.name": data.server_name,
                        "mcp.server.status": getattr(data.status, "value", str(data.status)),
                    },
                )
            elif isinstance(data, McpOauthRequiredData):
                session_errors.append(f"MCP OAuth required for {data.server_name}")
                span.add_event(
                    "mcp.oauth.required",
                    attributes={
                        "mcp.server.name": data.server_name,
                        "url.full": data.server_url,
                    },
                )
            elif isinstance(data, PermissionRequestedData):
                request = data.permission_request
                span.add_event(
                    "copilot.permission.request",
                    attributes={
                        "copilot.permission.request_id": data.request_id,
                        "copilot.permission.type": _event_text(getattr(request, "kind", "")),
                    },
                )
            elif isinstance(data, UserInputRequestedData):
                span.add_event(
                    "copilot.user_input.request",
                    attributes={
                        "copilot.user_input.request_id": data.request_id,
                        "copilot.user_input.question": _event_text(data.question),
                    },
                )
            elif isinstance(data, ExternalToolRequestedData):
                tool_calls.append(data.tool_name)
                span.add_event(
                    "gen_ai.tool.call",
                    attributes={
                        "gen_ai.tool.name": data.tool_name,
                        "gen_ai.tool.call.id": data.tool_call_id,
                        "gen_ai.tool.arguments": json.dumps(data.arguments or {}, default=str)[:1000],
                    },
                )
            elif isinstance(data, ToolExecutionStartData):
                tool_name = data.mcp_tool_name or data.tool_name
                tool_calls.append(tool_name)
                span.add_event(
                    "gen_ai.tool.call",
                    attributes={
                        "gen_ai.tool.name": tool_name,
                        "gen_ai.tool.call.id": data.tool_call_id,
                        "mcp.server.name": data.mcp_server_name or "",
                        "gen_ai.tool.arguments": json.dumps(data.arguments or {}, default=str)[:1000],
                    },
                )
            elif isinstance(data, ToolExecutionCompleteData):
                result_text = ""
                if data.result is not None:
                    result_text = (
                        getattr(data.result, "content", "")
                        or getattr(data.result, "detailed_content", "")
                        or ""
                    )
                error_message = getattr(data.error, "message", "") if data.error else ""
                span.add_event(
                    "gen_ai.tool.result",
                    attributes={
                        "gen_ai.tool.call.id": data.tool_call_id,
                        "gen_ai.tool.success": data.success,
                        "gen_ai.tool.result": result_text[:1000],
                        "error.message": _event_text(error_message),
                    },
                )
            elif isinstance(data, AssistantUsageData):
                usage_tokens["input"] += int(getattr(data, "input_tokens", 0) or 0)
                usage_tokens["output"] += int(getattr(data, "output_tokens", 0) or 0)
                m = getattr(data, "model", None)
                if m:
                    response_model.append(m)

        try:
            client = await _get_copilot_client()
            copilot_model, provider_config = await _copilot_provider_config()
            span.set_attribute("gen_ai.request.model", copilot_model)
            if provider_config:
                span.set_attribute("demo.copilot.provider", "azure-openai")
            session_kwargs = {
                "on_permission_request": PermissionHandler.approve_all,
                "on_user_input_request": _noninteractive_user_input,
                "model": copilot_model,
                "streaming": True,
            }
            create_session_params = inspect.signature(client.create_session).parameters
            if "on_event" in create_session_params:
                session_kwargs["on_event"] = on_event
            if provider_config and "provider" in create_session_params:
                session_kwargs["provider"] = provider_config
            mcp_servers = await _copilot_toolbox_mcp_servers()
            if mcp_servers:
                if "mcp_servers" in create_session_params:
                    session_kwargs["mcp_servers"] = mcp_servers
                    span.set_attribute("demo.copilot.toolbox.enabled", True)
                else:
                    span.set_attribute("demo.copilot.toolbox.enabled", False)
                    span.set_attribute("demo.copilot.toolbox.reason", "sdk_unsupported")
            else:
                span.set_attribute("demo.copilot.toolbox.enabled", False)
                span.set_attribute("demo.copilot.toolbox.reason", "endpoint_not_configured")

            async with await client.create_session(**session_kwargs) as session:
                if "on_event" not in create_session_params:
                    session.on(on_event)
                response_event = await session.send_and_wait(prompt, timeout=120)
                response_text = ""
                if response_event and isinstance(response_event.data, AssistantMessageData):
                    response_text = response_event.data.content or ""
                final_text = (response_text or "".join(assistant_final)).strip()
                delta_text = "".join(assistant_deltas).strip()
                text = final_text or delta_text
                if not text:
                    detail = session_errors[-1] if session_errors else "no assistant content received"
                    raise RuntimeError(f"Copilot returned an empty response: {detail}")
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            fallback = f"Copilot fallback unavailable: {e}"
            _set_span_output_message(span, fallback)
            return (
                f"### Copilot fallback (agent: github-copilot, "
                f"trace_id: {_current_trace_id_hex()})\n\n"
                f"_{fallback}_"
            )

        span.set_attribute("gen_ai.usage.input_tokens", usage_tokens["input"])
        span.set_attribute("gen_ai.usage.output_tokens", usage_tokens["output"])
        if response_model:
            span.set_attribute("gen_ai.response.model", response_model[-1])
        if tool_calls:
            span.set_attribute("gen_ai.tool.calls", json.dumps(tool_calls))
        _set_span_output_message(span, text)
        span.add_event(
            "gen_ai.choice",
            attributes={
                "gen_ai.system": "github-copilot",
                "gen_ai.message.content": text[:4000],
            },
        )

        return (
            f"### Copilot fallback (agent: github-copilot [{copilot_model}], "
            f"trace_id: {_current_trace_id_hex()})\n\n{text}"
        )


# --- Workflow messages and executors ---------------------------------------
# A single user query flows through the workflow as a CitySelection.
# Each selected specialist sends a CitySectionResult to the aggregator, which
# streams city sections as they complete and records the final combined output.

ALL_CITIES = ("seattle", "bengaluru", "xian", "copilot")


@dataclass
class CitySelection:
    """Output of the RouterExecutor: which specialists will handle the query."""

    query: str
    cities: tuple[str, ...] = ("copilot",)
    trace_context: dict[str, str] | None = None


@dataclass
class CitySectionResult:
    """Output from one specialist branch."""

    query: str
    city: str
    text: str


_DEMO_CHECKPOINT_TYPES = frozenset(
    {
        "__main__:CitySelection",
        "__main__:CitySectionResult",
        "main:CitySelection",
        "main:CitySectionResult",
        "orchestrator.main:CitySelection",
        "orchestrator.main:CitySectionResult",
    }
)
_original_checkpoint_storage_for_context = (
    _foundry_responses._checkpoint_storage_for_context
)


def _checkpoint_storage_for_context_with_demo_types(root: str, context_id: str):
    storage = _original_checkpoint_storage_for_context(root, context_id)
    storage._allowed_types = frozenset(  # noqa: SLF001 - hosting exposes no public hook.
        (*storage._allowed_types, *_DEMO_CHECKPOINT_TYPES)
    )
    return storage


_foundry_responses._checkpoint_storage_for_context = (
    _checkpoint_storage_for_context_with_demo_types
)


_CITY_KEYWORDS = {
    "seattle": ["seattle"],
    "bengaluru": ["bengaluru"],
    "xian": ["xi'an", "xian", "xi an"],
}


def _dedupe_cities(cities: list[str]) -> tuple[str, ...]:
    selected: list[str] = []
    for city in cities:
        if city in ALL_CITIES and city not in selected:
            selected.append(city)
    supported = [city for city in selected if city != "copilot"]
    if supported:
        return tuple(supported)
    return ("copilot",)


def _heuristic_route(query: str) -> tuple[str, ...]:
    """Fallback router when the LLM router fails. Picks every supported city
    mentioned in the user's order; defaults to ``copilot`` for anything else."""
    q = query.lower()
    matches: list[tuple[int, str]] = []
    for c, kws in _CITY_KEYWORDS.items():
        for k in kws:
            idx = q.find(k)
            if idx >= 0:
                matches.append((idx, c))
                break
    if not matches:
        return ("copilot",)
    return _dedupe_cities([city for _, city in sorted(matches, key=lambda item: item[0])])


class RouterExecutor(Executor):
    """LLM-driven router: picks all specialists needed for the request."""

    def __init__(self, agent: Agent, id: str = "router") -> None:
        super().__init__(id=id)
        self._agent = agent

    @handler
    async def route(
        self, messages: list[Message], ctx: WorkflowContext[CitySelection]
    ) -> None:
        # The Responses-host wraps the user input as a list[Message] chat history.
        # Use the latest user-authored message as the routing query.
        query = ""
        for m in reversed(messages):
            role = getattr(m, "role", None)
            role_str = getattr(role, "value", role) if role is not None else None
            if role_str in (None, "user", "User"):
                query = (getattr(m, "text", "") or "").strip()
                if query:
                    break
        if not query and messages:
            query = (getattr(messages[-1], "text", "") or "").strip()

        root_span = _start_demo_root_agent_span(query)
        root_context = (
            trace.use_span(root_span, end_on_exit=False)
            if _is_recording(root_span)
            else contextlib.nullcontext()
        )

        with root_context:
            _add_root_user_message(query)
            with tracer.start_as_current_span("router decision") as span:
                span.set_attribute("demo.query", query[:512])
                cities: tuple[str, ...] | None = None
                try:
                    result = await self._agent.run(query)
                    text = (getattr(result, "text", "") or "").strip()
                    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                    if match:
                        parsed = json.loads(match.group(0))
                        candidates = parsed.get("cities")
                        if isinstance(candidates, list):
                            cities = _dedupe_cities(
                                [c for c in candidates if isinstance(c, str)]
                            )
                        else:
                            candidate = parsed.get("city")
                            if isinstance(candidate, str):
                                cities = _dedupe_cities([candidate])
                except Exception as e:
                    span.record_exception(e)

                if not cities:
                    cities = _heuristic_route(query)
                    span.set_attribute("demo.router.fallback", "heuristic")
                span.set_attribute("demo.router.cities", json.dumps(list(cities)))
                span.set_attribute("demo.router.city_count", len(cities))
                ctx.set_state("expected_cities", list(cities))
                ctx.set_state("city_results", {})
                ctx.set_state("emitted_cities", [])

                trace_context: dict[str, str] = {}
                inject(trace_context)
                await ctx.send_message(
                    CitySelection(
                        query=query,
                        cities=cities,
                        trace_context=trace_context,
                    )
                )


def _make_specialist_executor(
    city: str, call: "Callable[[str], asyncio.Future[str]]"
):
    """Factory that builds an executor for one city specialist.

    The executor calls its underlying remote agent and sends the result to the
    aggregator. It does not yield directly; the aggregator owns streamed output.
    """

    class _SpecialistExecutor(Executor):
        @handler
        async def run(
            self,
            selection: CitySelection,
            ctx: WorkflowContext[CitySectionResult],
        ) -> None:
            parent_token = None
            try:
                if selection.trace_context:
                    parent_token = otel_context.attach(
                        extract(selection.trace_context)
                    )
                text = await call(selection.query)
            except Exception as e:
                text = f"_{city} specialist failed: {e}_"
            finally:
                if parent_token is not None:
                    otel_context.detach(parent_token)
            await ctx.send_message(
                CitySectionResult(query=selection.query, city=city, text=text)
            )

    _SpecialistExecutor.__name__ = f"{city.capitalize()}Executor"
    return _SpecialistExecutor(id=f"specialist_{city}")


class AggregatorExecutor(Executor):
    """Fan-in executor that combines all selected specialist outputs."""

    def __init__(self, id: str = "aggregator") -> None:
        super().__init__(id=id)

    @handler
    async def aggregate(
        self,
        result: CitySectionResult,
        ctx: WorkflowContext[Never, str],
    ) -> None:
        expected = ctx.get_state("expected_cities", [result.city])
        stored = dict(ctx.get_state("city_results", {}))
        stored[result.city] = result.text
        ctx.set_state("city_results", stored)

        emitted = list(ctx.get_state("emitted_cities", []))
        if result.city not in emitted:
            separator = "" if not emitted else "\n\n---\n\n"
            await ctx.yield_output(
                f"[city:{result.city}]\n{separator}{result.text}"
            )
            emitted.append(result.city)
            ctx.set_state("emitted_cities", emitted)

        if not all(city in stored for city in expected):
            return

        sections = [stored[city] for city in expected]
        _add_root_choice("\n\n---\n\n".join(sections))


# --- Hosted-agent entrypoint -----------------------------------------------

def _build_workflow_agent():
    """Construct the multi-agent workflow and wrap it as a hostable agent.

    Topology:

        RouterExecutor  --(cities contains "seattle")-->   SeattleExecutor
                        --(cities contains "bengaluru")--> BengaluruExecutor
                        --(cities contains "xian")------>  XianExecutor
                        --(else)------------------------>  CopilotExecutor
                                                             |
                                                             v
                                                        AggregatorExecutor

    The aggregator waits for all selected specialists and returns one combined
    Markdown response.
    """
    client = FoundryChatClient(
        project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
        model=MODEL_DEPLOYMENT,
        credential=DefaultAzureCredential(),
    )

    router_agent = Agent(
        client=client,
        name="city_router",
        instructions=ROUTER_INSTRUCTIONS,
    )

    router = RouterExecutor(router_agent)
    seattle = _make_specialist_executor("seattle", _specialist_seattle)
    bengaluru = _make_specialist_executor("bengaluru", _specialist_bengaluru)
    xian = _make_specialist_executor("xian", _specialist_xian)
    copilot = _make_specialist_executor("copilot", _specialist_copilot)
    aggregator = AggregatorExecutor()

    specialists = {
        "seattle": seattle,
        "bengaluru": bengaluru,
        "xian": xian,
        "copilot": copilot,
    }

    def _is(target_city: str):
        return lambda sel: target_city in getattr(sel, "cities", ())

    workflow = (
        WorkflowBuilder(
            name="any-agent-any-cloud-orchestrator",
            description=(
                "Routes a travel question to one or more city specialists "
                "(Seattle on AWS Lambda, Bengaluru on GCP Cloud Run, Xi'an on Foundry, "
                "or GitHub Copilot fallback)."
            ),
            start_executor=router,
            output_from=[aggregator],
        )
        .add_edge(router, seattle, condition=_is("seattle"))
        .add_edge(router, bengaluru, condition=_is("bengaluru"))
        .add_edge(router, xian, condition=_is("xian"))
        .add_edge(router, copilot, condition=_is("copilot"))
        .add_edge(seattle, aggregator)
        .add_edge(bengaluru, aggregator)
        .add_edge(xian, aggregator)
        .add_edge(copilot, aggregator)
        .build()
    )

    return workflow.as_agent(name=AGENT_NAME)


def main() -> None:
    observability.setup_observability(SERVICE_NAME, agent_framework=True)

    workflow_agent = _build_workflow_agent()
    server = ResponsesHostServer(workflow_agent)
    server.run()


if __name__ == "__main__":
    main()

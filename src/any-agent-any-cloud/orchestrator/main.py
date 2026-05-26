"""Foundry-hosted travel orchestrator (Microsoft Agent Framework Workflow).

The orchestrator is now a **multi-agent workflow** built with
``agent_framework.WorkflowBuilder``:

    user query
        |
        v
    RouterExecutor (LLM picks which cities are mentioned)
        |
        +-> SeattleExecutor    (AWS Lambda via boto3)
        +-> BangaloreExecutor  (GCP Cloud Run via HTTPS)
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
import json
import logging
import os
import re
from dataclasses import dataclass

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
from agent_framework.observability import enable_instrumentation
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from opentelemetry.propagate import inject
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
BANGALORE_AGENT_NAME = "bangalore_specialist"
COPILOT_AGENT_NAME = "copilot-fallback"

# OTel bootstrap (TracerProvider, Azure Monitor exporter, instrumentors,
# resource attributes) is configured by the Foundry hosted-agent runtime;
# we just grab a tracer from the global provider.
tracer = trace.get_tracer(SERVICE_NAME)

# The Foundry hosted runtime does NOT call AIProjectInstrumentor.instrument()
# for us, so the Projects SDK's GenAI spans + W3C trace-context propagation
# into Foundry OpenAI clients only activate after we call it explicitly.
# Per azure-ai-projects README:
#   - AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true must be set BEFORE this call
#     (env var is on agent.yaml).
#   - Trace context propagation is on by default once instrument() runs;
#     baggage propagation is gated by
#     AZURE_TRACING_GEN_AI_TRACE_CONTEXT_PROPAGATION_INCLUDE_BAGGAGE=true
#     (also on agent.yaml).
# Run before any get_openai_client() acquisition so the httpx hook is
# registered on every client we ever obtain.
try:
    from azure.ai.projects.telemetry import AIProjectInstrumentor

    AIProjectInstrumentor().instrument()
except Exception as _e:  # pragma: no cover
    logging.getLogger(__name__).warning(
        "AIProjectInstrumentor().instrument() failed: %s", _e
    )

# Silence Agent Framework workflow plumbing spans so the trace tree emphasizes
# user-meaningful GenAI work: router decision, model call, specialist dispatch,
# and remote specialist execution. The no-op context managers deliberately do
# not install a NonRecordingSpan as current context; child spans still parent
# under the visible current span while framework attributes/events are absorbed.
try:
    import contextlib as _contextlib
    import importlib as _importlib
    from agent_framework import observability as _af_observability
    from opentelemetry.trace import INVALID_SPAN_CONTEXT

    _HIDDEN_WORKFLOW_SPANS = {"workflow.run", "message.send"}
    _original_create_workflow_span = _af_observability.create_workflow_span

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
        if name in _HIDDEN_WORKFLOW_SPANS:
            return _silence_framework_span()
        return _original_create_workflow_span(name, *args, **kwargs)

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
    for _module_name in (
        "agent_framework._workflows._workflow",
        "agent_framework._workflows._functional",
        "agent_framework._workflows._workflow_builder",
        "agent_framework._workflows._workflow_context",
        "agent_framework._workflows._executor",
        "agent_framework._workflows._edge_runner",
    ):
        _patch_framework_span_helpers(_module_name)
except Exception as _e:  # pragma: no cover
    logging.getLogger(__name__).warning(
        "Failed to silence Agent Framework plumbing spans: %s", _e
    )


def _current_trace_id_hex() -> str:
    ctx = trace.get_current_span().get_span_context()
    if not ctx or not ctx.trace_id:
        return ""
    return format(ctx.trace_id, "032x")

# --- Specialist endpoints --------------------------------------------------
SEATTLE_LAMBDA_NAME = os.environ["SEATTLE_LAMBDA_NAME"]
SEATTLE_LAMBDA_REGION = os.getenv("SEATTLE_LAMBDA_REGION", "us-west-2")
SEATTLE_URL = os.getenv("SEATTLE_AGENT_URL", "")
BANGALORE_URL = os.getenv(
    "BANGALORE_AGENT_URL",
    os.getenv("KL_AGENT_URL", "http://localhost:8081"),
)
XIAN_AGENT_NAME = os.environ["XIAN_AGENT_NAME"]  # remote Foundry Prompt Agent
FOUNDRY_PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT = os.environ.get("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "devsecret")


ROUTER_INSTRUCTIONS = """You are a city router for a multi-agent travel concierge.

Given a customer's travel question, pick exactly ONE specialist to handle it.
You have FOUR specialists:

  * "seattle"  - Seattle, USA specialist (LangGraph on AWS Lambda)
  * "bangalore" - Bangalore, India specialist (Google ADK on GCP)
  * "xian"     - Xi'an, China specialist (Foundry Prompt Agent)
  * "copilot"  - General-purpose travel knowledge fallback (GitHub Copilot)

Rules:
- If the customer asks about Seattle, choose "seattle".
- If the customer asks about Bangalore or Bengaluru, choose "bangalore".
- If the customer asks about Xi'an, choose "xian".
- For any other single city (or no city at all), choose "copilot".
- If the customer mentions multiple supported cities at once, pick the
  one they ask about FIRST in the message.
- Never invent extra cities. Stick to the list.

Respond with ONLY a JSON object, no markdown fences, no commentary:

{"city": "seattle"}
"""


# --- Tool helpers ----------------------------------------------------------

async def _call_http_specialist(
    name: str, agent_name: str, url: str, query: str
) -> str:
    """Call a FastAPI specialist using its non-streaming `/plan` endpoint.

    Emits an OTel GenAI ``invoke_agent <agent_name>`` span and injects the
    W3C traceparent under that span so the specialist's server-side spans
    nest under this invocation (not under whatever was current upstream).
    """
    import httpx
    from opentelemetry.instrumentation.utils import suppress_http_instrumentation

    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "demo.target_agent": name,
            "http.url": f"{url}/plan",
        },
    ):
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": SHARED_SECRET,
        }
        # Inject traceparent INSIDE the invoke_agent span so the remote
        # server-side spans are parented under this invocation.
        inject(headers)
        # Suppress opentelemetry-instrumentation-httpx for this specific
        # POST. Otherwise it auto-creates an intermediate `POST` CLIENT
        # span and re-injects traceparent from that span — but in the
        # Foundry hosted runtime that intermediate span is silently
        # dropped before export to App Insights, leaving the remote
        # server-side `POST /plan` parented under a non-existent span.
        # With suppression, our manual `inject(headers)` above stays
        # intact, so the remote `POST /plan` nests directly under this
        # `invoke_agent` span.
        with suppress_http_instrumentation():
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    f"{url}/plan", headers=headers, json={"query": query}
                )
                r.raise_for_status()
                data = r.json()
        return (
            f"### {data.get('city', name)} "
            f"(agent: {data.get('agent', '')}, trace_id: {data.get('trace_id', '')})\n\n"
            f"{data.get('itinerary', '')}"
        )


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
    ):
        headers: dict[str, str] = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-demo-auth": SHARED_SECRET,
        }
        # Inject traceparent INSIDE the invoke_agent span so the remote
        # Lambda server-side spans are parented under this invocation.
        inject(headers)
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
        return (
            f"### {data.get('city', 'Seattle')} "
            f"(agent: {data.get('agent', '')}, trace_id: {data.get('trace_id', '')})\n\n"
            f"{data.get('itinerary', '')}"
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

    No manual span here: the Foundry OpenAI client (via
    ``AIProjectInstrumentor``) emits its own
    ``invoke_agent <xian-agent-name>`` span when ``responses.create`` is
    called with an ``agent_reference``. ``asyncio.to_thread`` copies the
    caller's context, so that auto span lands under whatever workflow
    span is currently active.
    """
    oa = _get_foundry_openai_client()

    def _run_sync() -> tuple[str, str]:
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
        return "".join(text_parts), conv.id

    text, _conv_id = await asyncio.to_thread(_run_sync)
    return (
        f"### Xi'an (agent: {XIAN_AGENT_NAME} [Foundry Prompt Agent v2], "
        f"trace_id: {_current_trace_id_hex()})\n\n{text}"
    )


# --- Tools exposed to the orchestrator agent --------------------------------

# --- Specialist callable wrappers ------------------------------------------
# These are thin async functions that just dispatch to the right transport.
# They are called by the workflow's specialist executors, not by an LLM.

async def _specialist_seattle(query: str) -> str:
    """Plan a trip in Seattle, USA via the LangGraph specialist on AWS Lambda."""
    if SEATTLE_URL:
        return await _call_http_specialist(
            "seattle", SEATTLE_AGENT_NAME, SEATTLE_URL, query
        )
    return await _call_seattle_lambda(query)


async def _specialist_bangalore(query: str) -> str:
    """Plan a trip in Bangalore, India via the ADK specialist on GCP."""
    return await _call_http_specialist(
        "bangalore", BANGALORE_AGENT_NAME, BANGALORE_URL, query
    )


async def _specialist_xian(query: str) -> str:
    """Plan a trip in Xi'an, China via the Xi'an Foundry Prompt Agent specialist."""
    return await _call_xian_foundry(query)


# --- Copilot SDK fallback for any other city -------------------------------
_copilot_client = None
_copilot_lock = asyncio.Lock()
COPILOT_MODEL = os.getenv("COPILOT_MODEL", "claude-sonnet-4.5")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")


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
        config = SubprocessConfig(
            github_token=GITHUB_TOKEN,
            telemetry=telemetry_cfg,
        ) if GITHUB_TOKEN else SubprocessConfig(telemetry=telemetry_cfg)
        client = CopilotClient(config)
        await client.start()
        _copilot_client = client
        return client


async def _specialist_copilot(query: str) -> str:
    """General-knowledge travel fallback powered by GitHub Copilot."""
    from copilot.generated.session_events import (
        AssistantMessageData,
        AssistantUsageData,
        SessionIdleData,
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
            "intro and bullet points. Do not call any tools.\n\n"
            f"Customer: {query}"
        )

        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "github-copilot",
                "gen_ai.message.content": query[:4000],
            },
        )

        try:
            client = await _get_copilot_client()
            async with await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                model=COPILOT_MODEL,
            ) as session:
                done = asyncio.Event()
                assistant_text: list[str] = []
                usage_tokens = {"input": 0, "output": 0}
                response_model: list[str] = []

                def on_event(event):
                    data = event.data
                    if isinstance(data, AssistantMessageData):
                        if data.content:
                            assistant_text.append(data.content)
                    elif isinstance(data, SessionIdleData):
                        done.set()
                    elif isinstance(data, AssistantUsageData):
                        usage_tokens["input"] += int(getattr(data, "input_tokens", 0) or 0)
                        usage_tokens["output"] += int(getattr(data, "output_tokens", 0) or 0)
                        m = getattr(data, "model", None)
                        if m:
                            response_model.append(m)

                session.on(on_event)
                await session.send(prompt)
                await asyncio.wait_for(done.wait(), timeout=120)
                text = "".join(assistant_text).strip()
        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.record_exception(e)
            return (
                f"### Copilot fallback (agent: github-copilot, "
                f"trace_id: {_current_trace_id_hex()})\n\n"
                f"_Copilot fallback unavailable: {e}_"
            )

        span.set_attribute("gen_ai.usage.input_tokens", usage_tokens["input"])
        span.set_attribute("gen_ai.usage.output_tokens", usage_tokens["output"])
        if response_model:
            span.set_attribute("gen_ai.response.model", response_model[-1])
        span.add_event(
            "gen_ai.choice",
            attributes={
                "gen_ai.system": "github-copilot",
                "gen_ai.message.content": text[:4000],
            },
        )

        return (
            f"### Copilot fallback (agent: github-copilot [{COPILOT_MODEL}], "
            f"trace_id: {_current_trace_id_hex()})\n\n{text}"
        )


# --- Workflow messages and executors ---------------------------------------
# A single user query flows through the workflow as a CityRoutingDecision.
# Each specialist executor sees the decision, calls its remote agent if its
# city is selected, otherwise emits an empty CitySectionResult so the
# fan-in aggregator can synchronize on all four sources.

ALL_CITIES = ("seattle", "bangalore", "xian", "copilot")


@dataclass
class CitySelection:
    """Output of the RouterExecutor: which single specialist will handle the query."""

    query: str
    city: str = "copilot"


_CITY_KEYWORDS = {
    "seattle": ["seattle"],
    "bangalore": ["bangalore", "bengaluru"],
    "xian": ["xi'an", "xian", "xi an"],
}


def _heuristic_route(query: str) -> str:
    """Fallback router when the LLM router fails. Picks first supported city
    by keyword; defaults to ``copilot`` for anything else."""
    q = query.lower()
    best: tuple[int, str] | None = None
    for c, kws in _CITY_KEYWORDS.items():
        for k in kws:
            idx = q.find(k)
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, c)
                break
    return best[1] if best else "copilot"


class RouterExecutor(Executor):
    """LLM-driven router: picks the ONE specialist to invoke."""

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

        with tracer.start_as_current_span("router decision") as span:
            span.set_attribute("demo.query", query[:512])
            city: str | None = None
            try:
                result = await self._agent.run(query)
                text = (getattr(result, "text", "") or "").strip()
                match = re.search(r"\{.*\}", text, flags=re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    candidate = parsed.get("city")
                    if candidate in ALL_CITIES:
                        city = candidate
            except Exception as e:
                span.record_exception(e)

            if not city:
                city = _heuristic_route(query)
                span.set_attribute("demo.router.fallback", "heuristic")
            span.set_attribute("demo.router.city", city)

        await ctx.send_message(CitySelection(query=query, city=city))


def _make_specialist_executor(
    city: str, call: "Callable[[str], asyncio.Future[str]]"
):
    """Factory that builds a TERMINAL Executor for one city specialist.

    The executor calls its underlying remote agent and yields the result
    directly as the workflow's output. No fan-in or aggregator is needed
    because exactly one specialist is reached per request.
    """

    class _SpecialistExecutor(Executor):
        @handler
        async def run(
            self,
            selection: CitySelection,
            ctx: WorkflowContext[Never, str],
        ) -> None:
            try:
                text = await call(selection.query)
            except Exception as e:
                text = f"_{city} specialist failed: {e}_"
            await ctx.yield_output(f"[city:{city}]\n{text}")

    _SpecialistExecutor.__name__ = f"{city.capitalize()}Executor"
    return _SpecialistExecutor(id=f"specialist_{city}")


# --- Hosted-agent entrypoint -----------------------------------------------

def _build_workflow_agent():
    """Construct the multi-agent workflow and wrap it as a hostable agent.

    Topology:

        RouterExecutor  --(city == "seattle")-->  SeattleExecutor  --> yield_output
                        --(city == "bangalore")>  BangaloreExecutor --> yield_output
                        --(city == "xian")---->  XianExecutor      --> yield_output
                        --(else)--------------->  CopilotExecutor  --> yield_output

    Each specialist is a terminal output executor; the workflow returns the
    moment a specialist finishes, with no fan-in or aggregation step.
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
    bangalore = _make_specialist_executor("bangalore", _specialist_bangalore)
    xian = _make_specialist_executor("xian", _specialist_xian)
    copilot = _make_specialist_executor("copilot", _specialist_copilot)

    specialists = {
        "seattle": seattle,
        "bangalore": bangalore,
        "xian": xian,
        "copilot": copilot,
    }

    def _is(target_city: str):
        return lambda sel: getattr(sel, "city", "") == target_city

    workflow = (
        WorkflowBuilder(
            name="any-agent-any-cloud-orchestrator",
            description=(
                "Routes a travel question to exactly one city specialist "
                "(Seattle on AWS Lambda, Bangalore on GCP Cloud Run, Xi'an on Foundry, "
                "or GitHub Copilot fallback)."
            ),
            start_executor=router,
            output_executors=list(specialists.values()),
        )
        .add_edge(router, seattle, condition=_is("seattle"))
        .add_edge(router, bangalore, condition=_is("bangalore"))
        .add_edge(router, xian, condition=_is("xian"))
        .add_edge(router, copilot, condition=_is("copilot"))
        .build()
    )

    return workflow.as_agent(name=AGENT_NAME)


def main() -> None:
    enable_instrumentation(enable_sensitive_data=True)

    workflow_agent = _build_workflow_agent()
    server = ResponsesHostServer(workflow_agent)
    server.run()


if __name__ == "__main__":
    main()

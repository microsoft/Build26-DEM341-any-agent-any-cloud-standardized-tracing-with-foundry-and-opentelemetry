"""Seattle travel specialist agent.

Stack: LangGraph on AWS Lambda + Azure Foundry model, FastAPI, OTel GenAI semconv.
"""
from __future__ import annotations

import asyncio
import os
import json
from typing import Annotated, AsyncIterator, TypedDict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from opentelemetry import context as otel_context, trace
from opentelemetry.propagate import extract
from pydantic import BaseModel

from telemetry import configure_telemetry, current_trace_id_hex, flush_telemetry

SERVICE_NAME = "seattle-langgraph"
AGENT_NAME = "seattle_specialist"
CITY = "Seattle"
REGION = os.getenv("AWS_REGION", "us-west-2")
MODEL_ID = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")

tracer = configure_telemetry(
    service_name=SERVICE_NAME,
    cloud_provider="aws",
    cloud_region=REGION,
    agent_name=AGENT_NAME,
    demo_city=CITY,
)

SYSTEM_PROMPT = """You are a Seattle travel specialist. Build a concise, day-by-day
travel plan for Seattle, WA. Include neighborhoods (Pike Place, Capitol Hill,
Ballard, Fremont), signature experiences (Space Needle, MoPOP, Chihuly, ferry
to Bainbridge), coffee/food highlights, and weather-aware tips. Markdown only."""


class State(TypedDict):
    query: str
    itinerary: str
    messages: list[BaseMessage]


_foundry_openai_client = None


def _get_foundry_openai_client():
    global _foundry_openai_client
    if _foundry_openai_client is None:
        from openai import AzureOpenAI

        if AZURE_OPENAI_API_KEY:
            _foundry_openai_client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY,
                api_version=AZURE_OPENAI_API_VERSION,
            )
        else:
            from azure.identity import ClientSecretCredential, get_bearer_token_provider

            credential = ClientSecretCredential(
                tenant_id=os.environ["AZURE_TENANT_ID"],
                client_id=os.environ["AZURE_CLIENT_ID"],
                client_secret=os.environ["AZURE_CLIENT_SECRET"],
            )
            token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )
            _foundry_openai_client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                azure_ad_token_provider=token_provider,
                api_version=AZURE_OPENAI_API_VERSION,
            )
    return _foundry_openai_client


def _extract_response_text(resp) -> str:
    output_text = getattr(resp, "output_text", None)
    if output_text:
        return output_text

    text_parts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", "") == "message":
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", "") or ""
                if text:
                    text_parts.append(text)
    return "".join(text_parts).strip()


def _run_foundry_model(query: str) -> str:
    client = _get_foundry_openai_client()
    resp = client.responses.create(
        model=MODEL_ID,
        instructions=SYSTEM_PROMPT,
        input=query,
        store=False,
    )
    return _extract_response_text(resp)


def _build_graph():
    from langgraph.graph import END, START, StateGraph

    def plan_node(state: State) -> State:
        query = state["query"]
        with tracer.start_as_current_span(
            "seattle.azure_foundry.invoke",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "az.ai.foundry",
                "gen_ai.request.model": MODEL_ID,
                "gen_ai.request.temperature": 0.4,
                "gen_ai.request.max_tokens": 1200,
            },
        ) as span:
            span.add_event(
                "gen_ai.user.message",
                attributes={
                    "gen_ai.system": "az.ai.foundry",
                    "gen_ai.message.content": query[:4000],
                },
            )
            text = _run_foundry_model(query)
            span.set_attribute("gen_ai.response.model", MODEL_ID)
            span.add_event(
                "gen_ai.choice",
                attributes={
                    "gen_ai.system": "az.ai.foundry",
                    "gen_ai.message.content": text[:4000],
                },
            )
            return {
                "query": query,
                "itinerary": text,
                "messages": [*state["messages"], AIMessage(content=text)],
            }

    g = StateGraph(State)
    g.add_node("plan", plan_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", END)
    return g.compile()


GRAPH = _build_graph()


async def _stream_foundry(query: str) -> AsyncIterator[str]:
    """Yield coarse text chunks from the Foundry model response."""
    text = await asyncio.to_thread(_run_foundry_model, query)
    for idx in range(0, len(text), 240):
        yield text[idx : idx + 240]


class PlanRequest(BaseModel):
    query: str
    trip_dates: str | None = None
    preferences: str | None = None


class PlanResponse(BaseModel):
    city: str
    itinerary: str
    agent: str
    trace_id: str


app = FastAPI(title="Seattle Specialist Agent")

SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "")


def _full_query(req: PlanRequest) -> str:
    full_query = req.query
    if req.trip_dates:
        full_query += f"\nDates: {req.trip_dates}"
    if req.preferences:
        full_query += f"\nPreferences: {req.preferences}"
    return full_query


def _extract_request_context(request: Request):
    headers = request.headers
    carrier = dict(headers)
    traceparent = headers.get("x-demo-traceparent")
    if traceparent:
        carrier["traceparent"] = traceparent
    tracestate = headers.get("x-demo-tracestate")
    if tracestate:
        carrier["tracestate"] = tracestate
    return extract(carrier)


@app.get("/healthz")
def healthz():
    return {"ok": True, "agent": AGENT_NAME, "city": CITY}


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, request: Request, x_demo_auth: str | None = Header(default=None)):
    if SHARED_SECRET and x_demo_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        parent_token = otel_context.attach(_extract_request_context(request))
        try:
            full_query = _full_query(req)
            result = GRAPH.invoke(
                {
                    "query": full_query,
                    "itinerary": "",
                    "messages": [HumanMessage(content=full_query)],
                }
            )
            response = PlanResponse(
                city=CITY,
                itinerary=result["itinerary"],
                agent=AGENT_NAME,
                trace_id=current_trace_id_hex(),
            )
            return response
        finally:
            otel_context.detach(parent_token)
    finally:
        # Lambda freezes the process between invocations; flush before return.
        flush_telemetry()


@app.post("/plan/stream")
async def plan_stream(
    req: PlanRequest, request: Request, x_demo_auth: str | None = Header(default=None)
):
    """Stream itinerary tokens as Server-Sent Events.

    Event format:
      event: meta  -> {"agent","city","trace_id"}
      event: delta -> {"text": "..."}
      event: done  -> {}
    """
    if SHARED_SECRET and x_demo_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    async def event_gen() -> AsyncIterator[bytes]:
        try:
            parent_token = otel_context.attach(_extract_request_context(request))
            try:
                meta = {
                    "agent": AGENT_NAME,
                    "city": CITY,
                    "trace_id": current_trace_id_hex(),
                }
                yield f"event: meta\ndata: {json.dumps(meta)}\n\n".encode()

                full_query = _full_query(req)
                async for delta in _stream_foundry(full_query):
                    payload = json.dumps({"text": delta})
                    yield f"event: delta\ndata: {payload}\n\n".encode()

                yield b"event: done\ndata: {}\n\n"
            finally:
                otel_context.detach(parent_token)
        finally:
            flush_telemetry()

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --- A2A (Agent-to-Agent) surface ------------------------------------------
# Additive: exposes an A2A agent card + JSON-RPC endpoint alongside /plan so a
# Foundry prompt agent can invoke this specialist over A2A. /plan is unchanged.
from a2a_support import build_agent_card, mount_a2a  # noqa: E402


async def _a2a_plan(query: str) -> str:
    def _run() -> str:
        result = GRAPH.invoke(
            {
                "query": query,
                "itinerary": "",
                "messages": [HumanMessage(content=query)],
            }
        )
        return result["itinerary"]

    return await asyncio.to_thread(_run)


mount_a2a(
    app,
    agent_card=build_agent_card(
        name=AGENT_NAME,
        description="Seattle travel specialist (LangGraph on AWS Lambda + Foundry model).",
        skill_id="plan_seattle_trip",
        skill_name="Plan a Seattle trip",
        skill_description="Build a concise, day-by-day Seattle, WA travel plan.",
        skill_tags=["travel", "seattle", "itinerary"],
        examples=[
            "Plan a rainy Seattle coffee stop and one iconic indoor activity.",
        ],
    ),
    plan_fn=_a2a_plan,
)

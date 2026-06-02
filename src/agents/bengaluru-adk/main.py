"""Bengaluru travel specialist agent.

Stack: Google ADK + Vertex AI (Gemini), FastAPI, OTel GenAI semconv.
"""
from __future__ import annotations

import os

import json
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import BaseModel

from telemetry import configure_telemetry, current_trace_id_hex

SERVICE_NAME = "bengaluru-adk"
AGENT_NAME = "bengaluru_specialist"
AGENT_ID = os.getenv("BENGALURU_AGENT_ID", "bengaluru-specialist-gcp")
CITY = "Bengaluru"
REGION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
MODEL_ID = os.getenv("VERTEX_MODEL_ID", "gemini-2.5-flash-lite")

tracer = configure_telemetry(
    service_name=SERVICE_NAME,
    cloud_provider="gcp",
    cloud_region=REGION,
    agent_name=AGENT_NAME,
    demo_city=CITY,
)


def _genai_messages_json(role: str, content: str) -> str:
    return json.dumps(
        [{"role": role, "parts": [{"type": "text", "content": content}]}]
    )


SYSTEM_PROMPT = """You are a Bengaluru travel specialist. Build a concise plan
for Bengaluru, India. Respect the user's scope exactly.

If the user asks for a concise evening, single-area, or traffic-aware plan,
choose one compact area and return only:
- one focus area
- up to three timed stops in that area
- one traffic tip

Do not add optional alternatives, detours, "if time permits" ideas, or venues
outside the chosen area for concise requests. For broader day-by-day requests,
cover Cubbon Park, Lalbagh, Bengaluru Palace, Church Street, Indiranagar, local
cafes and breweries, South Indian food (dosa, idli, filter coffee), and
practical tips (traffic, weather, Namma Metro, rideshare timing). Markdown
only."""


def _build_agent():
    from google.adk.agents import Agent

    return Agent(
        name=AGENT_NAME,
        model=MODEL_ID,
        instruction=SYSTEM_PROMPT,
        description="Bengaluru travel specialist",
    )


AGENT = _build_agent()


async def _run_agent(query: str) -> str:
    from google.adk.runners import InMemoryRunner
    from google.genai import types as gen_types

    runner = InMemoryRunner(agent=AGENT, app_name="bengaluru-adk")
    session = await runner.session_service.create_session(
        app_name="bengaluru-adk", user_id="demo"
    )
    final = ""
    async for event in runner.run_async(
        user_id="demo",
        session_id=session.id,
        new_message=gen_types.Content(
            role="user", parts=[gen_types.Part(text=query)]
        ),
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final or "(no response)"


async def _stream_agent(query: str) -> AsyncIterator[str]:
    """Yield text deltas as the ADK runner produces them."""
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.adk.runners import InMemoryRunner
    from google.genai import types as gen_types

    runner = InMemoryRunner(agent=AGENT, app_name="bengaluru-adk")
    session = await runner.session_service.create_session(
        app_name="bengaluru-adk", user_id="demo"
    )
    seen_text = ""
    async for event in runner.run_async(
        user_id="demo",
        session_id=session.id,
        new_message=gen_types.Content(
            role="user", parts=[gen_types.Part(text=query)]
        ),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    ):
        if not (event.content and event.content.parts):
            continue
        text = "".join(p.text or "" for p in event.content.parts)
        if not text:
            continue
        # ADK SSE may emit accumulating text per event; compute the delta.
        if text.startswith(seen_text):
            delta = text[len(seen_text):]
        else:
            delta = text
        if delta:
            seen_text = text if text.startswith(seen_text) else seen_text + delta
            yield delta


class PlanRequest(BaseModel):
    query: str
    trip_dates: str | None = None
    preferences: str | None = None


class PlanResponse(BaseModel):
    city: str
    itinerary: str
    agent: str
    trace_id: str


app = FastAPI(title="Bengaluru Specialist Agent")

SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "")
_TRACE_CONTEXT_PROPAGATOR = TraceContextTextMapPropagator()


def _server_span_attributes(request: Request, route: str) -> dict[str, str]:
    attrs = {
        "http.request.method": request.method,
        "http.route": route,
        "url.path": request.url.path,
        "gen_ai.agent.name": AGENT_NAME,
        "demo.city": CITY,
    }
    if request.url.hostname:
        attrs["server.address"] = request.url.hostname
    return attrs


def _extract_request_context(request: Request):
    headers = request.headers
    carrier: dict[str, str] = {}
    traceparent = headers.get("x-demo-traceparent") or headers.get("traceparent")
    if traceparent:
        carrier["traceparent"] = traceparent
    tracestate = headers.get("x-demo-tracestate") or headers.get("tracestate")
    if tracestate:
        carrier["tracestate"] = tracestate
    return _TRACE_CONTEXT_PROPAGATOR.extract(carrier)


@app.get("/healthz")
def healthz():
    return {"ok": True, "agent": AGENT_NAME, "city": CITY}


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, request: Request, x_demo_auth: str | None = Header(default=None)):
    if SHARED_SECRET and x_demo_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    server_context = _extract_request_context(request)
    with tracer.start_as_current_span(
        f"invoke_agent {AGENT_NAME}",
        kind=SpanKind.SERVER,
        context=server_context,
        attributes={
            **_server_span_attributes(request, "/plan"),
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.id": AGENT_ID,
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.system": "gcp.vertex_ai",
            "gen_ai.request.model": MODEL_ID,
            "demo.city": CITY,
        },
    ) as span:
        full_query = req.query
        if req.trip_dates:
            full_query += f"\nDates: {req.trip_dates}"
        if req.preferences:
            full_query += f"\nPreferences: {req.preferences}"

        span.set_attribute("demo.input.content", full_query)
        span.set_attribute("demo.input.length", len(full_query))
        span.set_attribute(
            "gen_ai.input.messages", _genai_messages_json("user", full_query)
        )
        span.add_event(
            "gen_ai.user.message",
            attributes={
                "gen_ai.system": "gcp.vertex_ai",
                "gen_ai.message.content": full_query[:4000],
            },
        )
        itinerary = await _run_agent(full_query)
        span.set_attribute("demo.output.content", itinerary)
        span.set_attribute("demo.output.length", len(itinerary))
        span.set_attribute(
            "gen_ai.output.messages", _genai_messages_json("assistant", itinerary)
        )
        span.add_event(
            "gen_ai.choice",
            attributes={
                "gen_ai.system": "gcp.vertex_ai",
                "gen_ai.message.content": itinerary[:4000],
            },
        )
        response = PlanResponse(
            city=CITY,
            itinerary=itinerary,
            agent=AGENT_NAME,
            trace_id=current_trace_id_hex(),
        )
        span.set_attribute("http.response.status_code", 200)
        span.set_attribute("http.status_code", 200)
    return response


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
        server_context = _extract_request_context(request)
        with tracer.start_as_current_span(
            f"invoke_agent {AGENT_NAME}",
            kind=SpanKind.SERVER,
            context=server_context,
            attributes={
                **_server_span_attributes(request, "/plan/stream"),
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": AGENT_NAME,
                "gen_ai.system": "gcp.vertex_ai",
                "gen_ai.request.model": MODEL_ID,
                "demo.city": CITY,
                "demo.streaming": True,
            },
        ) as span:
            meta = {
                "agent": AGENT_NAME,
                "city": CITY,
                "trace_id": current_trace_id_hex(),
            }
            yield f"event: meta\ndata: {json.dumps(meta)}\n\n".encode()

            full_query = req.query
            if req.trip_dates:
                full_query += f"\nDates: {req.trip_dates}"
            if req.preferences:
                full_query += f"\nPreferences: {req.preferences}"

            span.set_attribute("demo.input.content", full_query)
            span.set_attribute("demo.input.length", len(full_query))
            span.set_attribute(
                "gen_ai.input.messages", _genai_messages_json("user", full_query)
            )
            span.add_event(
                "gen_ai.user.message",
                attributes={
                    "gen_ai.system": "gcp.vertex_ai",
                    "gen_ai.message.content": full_query[:4000],
                },
            )
            output_chunks: list[str] = []
            first = True
            async for delta in _stream_agent(full_query):
                if first:
                    span.add_event("first_token")
                    first = False
                output_chunks.append(delta)
                payload = json.dumps({"text": delta})
                yield f"event: delta\ndata: {payload}\n\n".encode()

            output_text = "".join(output_chunks)
            span.set_attribute("demo.output.content", output_text)
            span.set_attribute("demo.output.length", len(output_text))
            span.set_attribute(
                "gen_ai.output.messages",
                _genai_messages_json("assistant", output_text),
            )
            span.add_event(
                "gen_ai.choice",
                attributes={
                    "gen_ai.system": "gcp.vertex_ai",
                    "gen_ai.message.content": output_text[:4000],
                },
            )
            span.add_event("last_token")
            span.set_attribute("http.response.status_code", 200)
            span.set_attribute("http.status_code", 200)
            yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --- A2A (Agent-to-Agent) surface ------------------------------------------
# Additive: exposes an A2A agent card + JSON-RPC endpoint alongside /plan so a
# Foundry prompt agent can invoke this specialist over A2A. /plan is unchanged.
from a2a_support import build_agent_card, mount_a2a  # noqa: E402


async def _a2a_plan(query: str) -> str:
    # No manual span here: the Microsoft distro's ADK instrumentation emits the
    # single canonical `invoke_agent bengaluru_specialist` span. A telemetry
    # span processor copies gen_ai input/output messages from the model span
    # onto that span (see telemetry._install_agent_span_processors).
    return await _run_agent(query)


mount_a2a(
    app,
    agent_card=build_agent_card(
        name=AGENT_NAME,
        description="Bengaluru travel specialist (Google ADK + Vertex AI on GCP Cloud Run).",
        skill_id="plan_bengaluru_trip",
        skill_name="Plan a Bengaluru trip",
        skill_description="Build a concise, day-by-day Bengaluru, India travel plan.",
        skill_tags=["travel", "bengaluru", "itinerary"],
        examples=[
            "Give one concise Bengaluru coffee and culture recommendation.",
        ],
    ),
    plan_fn=_a2a_plan,
)

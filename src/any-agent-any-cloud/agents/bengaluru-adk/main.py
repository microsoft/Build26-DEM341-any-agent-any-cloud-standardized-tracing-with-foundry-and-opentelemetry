"""Bengaluru travel specialist agent.

Stack: Google ADK + Vertex AI (Gemini), FastAPI, OTel GenAI semconv.
"""
from __future__ import annotations

import os

import json
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from telemetry import configure_telemetry, current_trace_id_hex

SERVICE_NAME = "bengaluru-adk"
AGENT_NAME = "bengaluru_specialist"
CITY = "Bengaluru"
REGION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
MODEL_ID = os.getenv("VERTEX_MODEL_ID", "gemini-2.0-flash-001")

tracer = configure_telemetry(
    service_name=SERVICE_NAME,
    cloud_provider="gcp",
    cloud_region=REGION,
    agent_name=AGENT_NAME,
    demo_city=CITY,
)

SYSTEM_PROMPT = """You are a Bengaluru travel specialist. Build a
concise, day-by-day plan for Bengaluru, India. Cover Cubbon Park, Lalbagh,
Bengaluru Palace, Church Street, Indiranagar, local cafes and breweries,
South Indian food (dosa, idli, filter coffee), and practical tips (traffic,
weather, Namma Metro, rideshare timing). Markdown only."""


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
FastAPIInstrumentor.instrument_app(app)

SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "")


@app.get("/healthz")
def healthz():
    return {"ok": True, "agent": AGENT_NAME, "city": CITY}


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest, request: Request, x_demo_auth: str | None = Header(default=None)):
    if SHARED_SECRET and x_demo_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")

    # No `context=...`: the FastAPI middleware already extracted the
    # upstream W3C traceparent from request headers and set the
    # `POST /plan` server span as the current context, so `bengaluru.plan`
    # naturally becomes a child of that server span.
    with tracer.start_as_current_span(
        "bengaluru.plan",
        attributes={
            "gen_ai.operation.name": "agent",
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.system": "gcp.vertex_ai",
            "gen_ai.request.model": MODEL_ID,
            "demo.city": CITY,
        },
    ):
        full_query = req.query
        if req.trip_dates:
            full_query += f"\nDates: {req.trip_dates}"
        if req.preferences:
            full_query += f"\nPreferences: {req.preferences}"

        itinerary = await _run_agent(full_query)
        return PlanResponse(
            city=CITY,
            itinerary=itinerary,
            agent=AGENT_NAME,
            trace_id=current_trace_id_hex(),
        )


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
        # No `context=...`: inherit from FastAPI's `POST /plan/stream`
        # server span (which itself parents under the upstream
        # invoke_agent span via the extracted traceparent).
        with tracer.start_as_current_span(
            "bengaluru.plan.stream",
            attributes={
                "gen_ai.operation.name": "agent",
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

            first = True
            async for delta in _stream_agent(full_query):
                if first:
                    span.add_event("first_token")
                    first = False
                payload = json.dumps({"text": delta})
                yield f"event: delta\ndata: {payload}\n\n".encode()

            span.add_event("last_token")
            yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")

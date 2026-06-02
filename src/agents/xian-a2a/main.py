"""Xi'an travel specialist — standalone A2A/HTTP server (Azure).

Stack: FastAPI + Azure OpenAI (gpt-5.4), hosted on Azure Container Apps. This is
the "third cloud" specialist of the Any-Agent-Any-Cloud demo (Seattle=AWS,
Bengaluru=GCP, Xi'an=Azure).

**Tracing is intentionally disabled.** No OpenTelemetry TracerProvider, exporter
or instrumentation is configured in this process. The orchestrators invoke this
agent over plain HTTP (``POST /plan``) or A2A and wrap the call in their own
``invoke_agent xian-specialist`` CLIENT span, so exactly one such span is emitted
per request. Running this agent untraced avoids (a) the azure-ai-projects
``AIProjectInstrumentor`` ``NonRecordingSpan`` crash that the Foundry
prompt-agent ``responses.create(agent_reference=...)`` path hits under concurrent
fan-out, and (b) the orphaned second-root subtree a separately-traced specialist
would add to the shared trace.

The Xi'an system prompt is loaded from ``prompt.txt`` (declarative, file-based),
and the model is called directly via the Azure OpenAI Responses API — never via
the Foundry agent_reference path — so the buggy responses instrumentor is never
exercised even if the host later enables tracing.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

SERVICE_NAME = "xian-a2a"
AGENT_NAME = "xian-specialist"
CITY = "Xi'an"
MODEL_ID = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
TEMPERATURE = float(os.getenv("XIAN_TEMPERATURE", "0.4"))
MAX_TOKENS = int(os.getenv("XIAN_MAX_TOKENS", "1200"))

SYSTEM_PROMPT = (Path(__file__).with_name("prompt.txt")).read_text(encoding="utf-8").strip()

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import AzureOpenAI

        if AZURE_OPENAI_API_KEY:
            _client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY,
                api_version=AZURE_OPENAI_API_VERSION,
            )
        else:
            from azure.identity import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            _client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                azure_ad_token_provider=token_provider,
                api_version=AZURE_OPENAI_API_VERSION,
            )
    return _client


def _extract_response_text(resp) -> str:
    output_text = getattr(resp, "output_text", None)
    if output_text:
        return output_text
    parts: list[str] = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", "") == "message":
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", "") or ""
                if text:
                    parts.append(text)
    return "".join(parts).strip()


def _run_model(query: str) -> str:
    client = _get_client()
    resp = client.responses.create(
        model=MODEL_ID,
        instructions=SYSTEM_PROMPT,
        input=query,
        max_output_tokens=MAX_TOKENS,
        store=False,
    )
    return _extract_response_text(resp)


class PlanRequest(BaseModel):
    query: str
    trip_dates: str | None = None
    preferences: str | None = None


class PlanResponse(BaseModel):
    city: str
    itinerary: str
    agent: str
    trace_id: str


app = FastAPI(title="Xi'an Specialist Agent")

SHARED_SECRET = os.getenv("DEMO_SHARED_SECRET", "")


def _full_query(req: PlanRequest) -> str:
    full_query = req.query
    if req.trip_dates:
        full_query += f"\nDates: {req.trip_dates}"
    if req.preferences:
        full_query += f"\nPreferences: {req.preferences}"
    return full_query


@app.get("/healthz")
def healthz():
    return {"ok": True, "agent": AGENT_NAME, "city": CITY}


@app.post("/plan", response_model=PlanResponse)
async def plan(
    req: PlanRequest,
    request: Request,
    x_demo_auth: str | None = Header(default=None),
):
    if SHARED_SECRET and x_demo_auth != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")
    itinerary = await asyncio.to_thread(_run_model, _full_query(req))
    return PlanResponse(city=CITY, itinerary=itinerary, agent=AGENT_NAME, trace_id="")


# --- A2A (Agent-to-Agent) surface ------------------------------------------
# Additive: exposes an A2A agent card + JSON-RPC endpoint alongside /plan so the
# agent can also be invoked over A2A. /plan stays the orchestrator transport.
from a2a_support import build_agent_card, mount_a2a  # noqa: E402


async def _a2a_plan(query: str) -> str:
    return await asyncio.to_thread(_run_model, query)


mount_a2a(
    app,
    agent_card=build_agent_card(
        name=AGENT_NAME,
        description="Xi'an travel specialist (Azure-hosted, Foundry gpt-5.4 model).",
        skill_id="plan_xian_trip",
        skill_name="Plan a Xi'an trip",
        skill_description="Build a concise, day-by-day Xi'an, China travel plan.",
        skill_tags=["travel", "xian", "itinerary"],
        examples=["Plan a one-day cultural trip to Xi'an with the Terracotta Army."],
    ),
    plan_fn=_a2a_plan,
)

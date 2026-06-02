"""Foundry hosted orchestrator (openai-agents SDK) for "Any Agent, Any Cloud".

Routes a travel query to the matching city specialist(s) — Seattle (LangGraph on
AWS Lambda), Bengaluru (Google ADK on GCP Cloud Run), Xi'an (Foundry prompt
agent) — running them in parallel when several cities are mentioned, then
synthesizes one combined Markdown answer.

Transport to each specialist lives in ``specialists.py`` (already emits the
``invoke_agent <specialist>`` CLIENT span + W3C traceparent). Observability
bootstrap lives in ``tracing.py``. This module just wires the agent, its three
tools, the Foundry-authenticated model client, and the responses host.
"""
from __future__ import annotations

import os

import tracing  # import first: sets GenAI env defaults before frameworks load

from agents import (
    Agent,
    Runner,
    function_tool,
    set_default_openai_api,
    set_default_openai_client,
)
from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)
from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI

import specialists

MODEL_DEPLOYMENT = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").rstrip("/")

# Point the openai-agents SDK at the Foundry project's OpenAI v1 surface, authed
# with Entra ID (no API keys). The async token provider is refreshed per request.
_token_provider = get_bearer_token_provider(
    DefaultAzureCredential(), "https://ai.azure.com/.default"
)
_openai_client = AsyncOpenAI(
    base_url=f"{FOUNDRY_PROJECT_ENDPOINT}/openai/v1",
    api_key=_token_provider,
)
set_default_openai_client(_openai_client, use_for_tracing=False)
set_default_openai_api("responses")


@function_tool
async def seattle_plan(request: str) -> str:
    """Plan a trip in Seattle, USA (LangGraph specialist on AWS Lambda)."""
    return await specialists.plan_seattle(request)


@function_tool
async def bengaluru_plan(request: str) -> str:
    """Plan a trip in Bengaluru, India (Google ADK specialist on GCP Cloud Run)."""
    return await specialists.plan_bengaluru(request)


@function_tool
async def xian_plan(request: str) -> str:
    """Plan a trip in Xi'an, China (Xi'an Foundry prompt agent specialist)."""
    return await specialists.plan_xian(request)


_INSTRUCTIONS = (
    "You are the 'Any Agent, Any Cloud' travel orchestrator. For each city the "
    "user mentions, call exactly its matching tool: Seattle -> seattle_plan, "
    "Bengaluru -> bengaluru_plan, Xi'an -> xian_plan. When several cities are "
    "mentioned, call all the relevant tools in parallel in a single turn. Never "
    "call a tool for a city the user did not mention, and never invent "
    "itineraries yourself. Synthesize the tool outputs into one Markdown answer "
    "with a '## <City>' section per city, preserving each specialist's content."
)

orchestrator_agent = Agent(
    name="openai-agents-orchestrator",
    instructions=_INSTRUCTIONS,
    tools=[seattle_plan, bengaluru_plan, xian_plan],
    model=MODEL_DEPLOYMENT,
)

app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20)
)

# Instrument openai-agents AFTER the host is constructed: the agentserver runtime
# owns the global TracerProvider (Foundry enrichment + Azure Monitor / A365
# export), and we attach the orchestrator's spans to it without overriding it.
tracing.setup_tracing()


@app.response_handler
async def handle_response(
    request: CreateResponse,
    context: ResponseContext,
    _cancellation_signal,
):
    user_text = (await context.get_input_text() or "").strip()
    if not user_text:
        user_text = "What can you help me plan?"

    async def run_orchestrator() -> str:
        result = await Runner.run(orchestrator_agent, user_text)
        return str(result.final_output or "")

    return TextResponse(context, request, text=run_orchestrator)


if __name__ == "__main__":
    app.run()

"""Deepagents travel orchestrator (Foundry hosted-agent, Responses protocol).

Routes a user travel query to the matching city specialist(s) and runs them in
parallel when multiple cities are mentioned. The three specialists live behind
``specialists.py`` (AWS Lambda / GCP Cloud Run / Foundry prompt agent); each
already emits its own ``invoke_agent <city>_specialist`` CLIENT span, so the
tools here are thin awaiters that keep the trace clean:

    invoke_agent deepagents-orchestrator   (root, deepagents)
      -> execute_tool <city>_plan          (framework tool span)
        -> invoke_agent <city>_specialist  (specialists.py)
          -> remote subtree
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import tracing  # import first: sets GenAI env defaults before frameworks load

from azure.identity import DefaultAzureCredential
from deepagents import create_deep_agent
from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind

from azure.ai.agentserver.responses import (
    CreateResponse,
    ResponseContext,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    TextResponse,
)

logger = logging.getLogger(__name__)

SERVICE_NAME = "deepagents-orchestrator"

FOUNDRY_PROJECT_ENDPOINT = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
AZURE_AI_MODEL_DEPLOYMENT_NAME = os.environ.get(
    "AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4"
)

SYSTEM_PROMPT = """\
You are a multi-cloud travel orchestrator. Exactly three city specialists are
available, each exposed as a tool:

- `seattle_plan`   — Seattle, USA
- `bengaluru_plan` — Bengaluru, India
- `xian_plan`      — Xi'an, China

Routing rules:
- If the user mentions Seattle, call `seattle_plan`.
- If the user mentions Bengaluru, call `bengaluru_plan`.
- If the user mentions Xi'an, Xian, or Xi’an, call `xian_plan`.
- If the user asks for "each", "all three", "three trip legs", or lists several
  supported cities, call every matching city tool. Do not let one city
  specialist answer for another city.
- When several cities are mentioned, emit all required tool calls in the same
  assistant turn so they run in parallel.
- Pass each tool a request that preserves the user's original intent and says
  that the specialist should answer for that city only.

After the tools return, synthesize one combined Markdown answer with a separate
`##` section per city, preserving each specialist's itinerary. If the user asks
about any city outside these three, politely explain that only Seattle,
Bengaluru and Xi'an are supported.
"""


def _genai_messages_json(role: str, content: str) -> str:
    return json.dumps(
        [{"role": role, "parts": [{"type": "text", "content": content}]}],
        ensure_ascii=False,
    )


def _is_recording(span: object) -> bool:
    is_recording = getattr(span, "is_recording", None)
    return bool(callable(is_recording) and is_recording())


def _set_span_input_message(span: object, content: str) -> None:
    if _is_recording(span):
        span.set_attribute("gen_ai.input.messages", _genai_messages_json("user", content))
        span.set_attribute("demo.input.content", content)


def _set_span_output_message(span: object, content: str) -> None:
    if _is_recording(span):
        span.set_attribute(
            "gen_ai.output.messages", _genai_messages_json("assistant", content)
        )
        span.set_attribute("demo.output.content", content)


def _annotate_tool_span(tool_name: str, target_agent: str, request: str) -> None:
    """Add missing tool-call args to the active framework execute_tool span."""
    span = otel_trace.get_current_span()
    if not _is_recording(span):
        return
    span.set_attribute(
        "gen_ai.tool.call.arguments",
        json.dumps({"request": request}, ensure_ascii=False),
    )
    span.set_attribute("gen_ai.input.messages", _genai_messages_json("user", request))
    span.set_attribute("demo.tool.name", tool_name)
    span.set_attribute("demo.target_agent", target_agent)


@tool
async def seattle_plan(request: str) -> str:
    """Plan a trip in Seattle, USA. Pass the user's full Seattle-specific request."""
    import specialists

    _annotate_tool_span("seattle_plan", "seattle_specialist", request)
    return await specialists.plan_seattle(request)


@tool
async def bengaluru_plan(request: str) -> str:
    """Plan a trip in Bengaluru, India. Pass the user's full Bengaluru-specific request."""
    import specialists

    _annotate_tool_span("bengaluru_plan", "bengaluru_specialist", request)
    return await specialists.plan_bengaluru(request)


@tool
async def xian_plan(request: str) -> str:
    """Plan a trip in Xi'an, China. Pass the user's full Xi'an-specific request."""
    import specialists

    _annotate_tool_span("xian_plan", "xian-specialist", request)
    return await specialists.plan_xian(request)


TOOLS = [seattle_plan, bengaluru_plan, xian_plan]


def _build_model() -> AzureAIOpenAIApiChatModel:
    return AzureAIOpenAIApiChatModel(
        project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
        model=AZURE_AI_MODEL_DEPLOYMENT_NAME,
        streaming=True,
    )


AGENT = create_deep_agent(
    model=_build_model(),
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,
    name=SERVICE_NAME,
)


app = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20)
)

# Enable LangChain GenAI instrumentation AFTER the host is constructed: the
# agentserver runtime owns the global TracerProvider (Foundry enrichment +
# export), and we attach the deep agent's spans to it without overriding it.
TRACING_CALLBACKS = tracing.setup_tracing(SERVICE_NAME)


@app.response_handler
async def handle_create(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    """Run the deep agent for one user turn and stream the combined answer."""

    async def run_agent():
        try:
            user_input = await context.get_input_text() or "Hello!"
            config = {"callbacks": TRACING_CALLBACKS} if TRACING_CALLBACKS else None
            tracer = otel_trace.get_tracer(SERVICE_NAME)
            with tracer.start_as_current_span(
                f"invoke_agent {SERVICE_NAME}",
                kind=SpanKind.CLIENT,
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "gen_ai.agent.name": SERVICE_NAME,
                    "demo.manual_root": True,
                },
            ) as span:
                _set_span_input_message(span, user_input)
                result = await AGENT.ainvoke(
                    {"messages": [HumanMessage(content=user_input)]}, config=config
                )
                raw = result["messages"][-1].content
                if isinstance(raw, list):
                    text = "".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in raw
                    )
                else:
                    text = raw or ""
                _set_span_output_message(span, text)
                yield text
        except Exception as exc:  # surface errors to the caller instead of hanging
            logger.exception("deep agent run failed")
            yield f"[ERROR] {type(exc).__name__}: {exc}"

    return TextResponse(context, request, text=run_agent())


if __name__ == "__main__":
    app.run()

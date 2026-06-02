"""Additive A2A (Agent-to-Agent protocol) surface for a specialist agent.

This mounts an A2A agent card (``/.well-known/agent-card.json``) and a JSON-RPC
endpoint (``/a2a``) onto the existing FastAPI app **without** touching the
original ``/plan`` REST routes, so the current orchestrator story keeps working
unchanged. A Foundry prompt agent can register this endpoint as an
``A2ATool(base_url=...)`` and invoke the specialist over A2A.

Trace context: Foundry / the A2A client injects W3C ``traceparent`` on the
JSON-RPC POST. The executor extracts it and attaches it as the current OTel
context before running the plan, so the specialist's spans nest under the
caller's ``invoke_agent`` span exactly like the ``/plan`` path.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable, Sequence

from opentelemetry import context as otel_context
from opentelemetry.propagate import extract

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    TransportProtocol,
)
from a2a.utils import new_agent_text_message

PlanFn = Callable[[str], Awaitable[str]]

A2A_RPC_PATH = "/a2a"
A2A_CARD_PATH = "/.well-known/agent-card.json"


class _SpecialistExecutor(AgentExecutor):
    """Bridges an A2A ``message/send`` to the specialist's existing plan logic."""

    def __init__(self, plan_fn: PlanFn) -> None:
        self._plan_fn = plan_fn

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = context.get_user_input()
        headers: dict[str, str] = {}
        if context.call_context and context.call_context.state:
            headers = context.call_context.state.get("headers", {}) or {}
        token = otel_context.attach(extract(headers))
        try:
            text = await self._plan_fn(query)
        finally:
            otel_context.detach(token)
        await event_queue.enqueue_event(new_agent_text_message(text))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported for this agent")


def build_agent_card(
    *,
    name: str,
    description: str,
    skill_id: str,
    skill_name: str,
    skill_description: str,
    skill_tags: Sequence[str],
    examples: Sequence[str],
    public_base_url: str | None = None,
) -> AgentCard:
    """Build the A2A agent card.

    ``url`` must be the externally reachable JSON-RPC endpoint. It is taken from
    ``public_base_url`` (or the ``A2A_PUBLIC_BASE_URL`` env var) plus the RPC
    path; deployment scripts set that to the Lambda Function URL / Cloud Run URL.
    """
    base = (public_base_url or os.getenv("A2A_PUBLIC_BASE_URL", "")).rstrip("/")
    return AgentCard(
        name=name,
        description=description,
        url=f"{base}{A2A_RPC_PATH}",
        version="1.0.0",
        preferred_transport=TransportProtocol.jsonrpc,
        default_input_modes=["text/plain"],
        default_output_modes=["text/markdown"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id=skill_id,
                name=skill_name,
                description=skill_description,
                tags=list(skill_tags),
                examples=list(examples),
            )
        ],
    )


def mount_a2a(app, *, agent_card: AgentCard, plan_fn: PlanFn) -> None:
    """Add the A2A card + JSON-RPC routes to an existing FastAPI ``app``."""
    handler = DefaultRequestHandler(
        agent_executor=_SpecialistExecutor(plan_fn),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AFastAPIApplication(agent_card=agent_card, http_handler=handler)
    a2a_app.add_routes_to_app(
        app,
        agent_card_url=A2A_CARD_PATH,
        rpc_url=A2A_RPC_PATH,
    )

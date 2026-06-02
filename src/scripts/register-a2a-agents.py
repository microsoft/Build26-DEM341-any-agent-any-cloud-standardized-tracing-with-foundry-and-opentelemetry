"""Register the A2A multi-cloud routing agent (and external specialists) in Foundry.

Requires **azure-ai-projects >= 2.2.0**.

This script does two things:

1. Registers each cross-cloud specialist (Seattle on AWS, Bengaluru on GCP) as a
   Foundry **external agent** via ``ExternalAgentDefinition(otel_agent_id=...)``.
   Registration is metadata-only: Foundry records the agent so the specialist's
   own customer-emitted OpenTelemetry spans — matched by
   ``gen_ai.agent.id == otel_agent_id`` — light up in the Foundry trace and
   evaluation experiences, even though the agent runs entirely outside Foundry.

2. Creates a NEW Foundry prompt agent (default name ``routing-a2a``) that invokes
   those specialists over the **A2A protocol** via ``A2APreviewTool(base_url=...)``
   — pointing at the specialists' public agent cards
   (``/.well-known/agent-card.json``). The showcase hosted orchestrator calls
   the same specialists over HTTP and is left untouched by this registration.

SDK notes (azure-ai-projects 2.2.0):
  * ``A2ATool`` was renamed to ``A2APreviewTool`` (2.0.1).
  * ``ExternalAgentDefinition`` (2.2.0) registers third-party agents for
    observability/eval over customer-emitted OTel data.
  * Registration uses ``project.agents.create_version()`` (NOT the legacy
    Assistants API). No RemoteTool connection is required: the specialist A2A
    endpoints are public, and ``A2APreviewTool.project_connection_id`` is optional.

Env:
  AZURE_AI_PROJECT_ENDPOINT / FOUNDRY_PROJECT_ENDPOINT  (required)
  SEATTLE_AGENT_URL    public base URL of the Seattle A2A endpoint
  BENGALURU_AGENT_URL  public base URL of the Bengaluru A2A endpoint
  ROUTING_A2A_AGENT_NAME   (optional, default "routing-a2a")
  FOUNDRY_MODEL            (optional, default "gpt-5.4")
  REGISTER_EXTERNAL_SPECIALISTS  (optional, default "1"; set "0" to skip step 1)
  XIAN_AGENT_NAME / XIAN_AGENT_VERSION  (optional; adds Xi'an as a connected
                          Foundry agent so the three-city story still works)
"""
from __future__ import annotations

import os
import sys

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    A2APreviewTool,
    AzureAIAgentTarget,
    ExternalAgentDefinition,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential

AGENT_CARD_PATH = "/.well-known/agent-card.json"

# Foundry external-agent name -> otel_agent_id emitted by the specialist on its
# ``invoke_agent`` boundary span (gen_ai.agent.id). These ids must match what the
# specialists stamp on their spans for the registration to attribute their traces.
EXTERNAL_SPECIALISTS = {
    "seattle-specialist": "seattle-specialist-aws",
    "bengaluru-specialist": "bengaluru-specialist-gcp",
}

INSTRUCTIONS = """\
You are a multi-cloud travel routing agent. The user asks for travel plans for
one or more cities. Route each city to the matching specialist tool and return
their answers:

- Seattle  -> call the `seattle_specialist` A2A tool.
- Bengaluru -> call the `bengaluru_specialist` A2A tool.
- Xi'an     -> call the `xian-specialist` connected agent (if available).

If the user lists multiple cities, call each matching tool and combine the
results under a clear per-city heading. Keep the routing deterministic: only
call the tool(s) for the cities the user actually mentioned. Do not invent
recommendations yourself for supported cities — always use the tool output.
Output Markdown."""


def _endpoint() -> str:
    ep = os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ.get(
        "FOUNDRY_PROJECT_ENDPOINT"
    )
    if not ep:
        sys.exit("ERROR: set AZURE_AI_PROJECT_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT")
    return ep


def build_tools() -> list:
    tools: list = []

    seattle_url = (os.environ.get("SEATTLE_AGENT_URL") or "").rstrip("/")
    bengaluru_url = (os.environ.get("BENGALURU_AGENT_URL") or "").rstrip("/")

    if seattle_url:
        print(f"  + A2A seattle_specialist  -> {seattle_url}{AGENT_CARD_PATH}")
        tools.append(
            A2APreviewTool(base_url=seattle_url, agent_card_path=AGENT_CARD_PATH)
        )
    else:
        print("  WARN: SEATTLE_AGENT_URL not set; skipping Seattle A2A tool")

    if bengaluru_url:
        print(f"  + A2A bengaluru_specialist -> {bengaluru_url}{AGENT_CARD_PATH}")
        tools.append(
            A2APreviewTool(base_url=bengaluru_url, agent_card_path=AGENT_CARD_PATH)
        )
    else:
        print("  WARN: BENGALURU_AGENT_URL not set; skipping Bengaluru A2A tool")

    # Optional: Xi'an as a connected Foundry agent. NOTE: in this project the
    # responses-protocol invocation path rejects the `azure_ai_agent` tool type
    # ("Invalid value: 'azure_ai_agent'") even though create_version accepts it,
    # so this is OFF by default. The new A2A routing agent focuses on the
    # Seattle/Bengaluru A2A path; Xi'an stays served by the showcase
    # orchestrator story. Set ROUTING_A2A_INCLUDE_XIAN=1 to experiment.
    if os.environ.get("ROUTING_A2A_INCLUDE_XIAN") == "1":
        xian_name = os.environ.get("XIAN_AGENT_NAME")
        xian_version = os.environ.get("XIAN_AGENT_VERSION")
        if xian_name:
            try:
                tools.append(
                    AzureAIAgentTarget(
                        name=xian_name,
                        version=str(xian_version) if xian_version else None,
                    )
                )
                print(f"  + connected agent xian -> {xian_name}:{xian_version}")
            except Exception as e:  # noqa: BLE001
                print(f"  WARN: could not add Xi'an connected agent: {e}")

    return tools


def register_external_specialists(project: AIProjectClient) -> None:
    """Register each cross-cloud specialist as a Foundry *external* agent.

    Metadata-only (``ExternalAgentDefinition``): Foundry records the agent so its
    customer-emitted OpenTelemetry spans (matched by
    ``gen_ai.agent.id == otel_agent_id``) light up in the trace and evaluation
    experiences, even though the specialist runs on AWS / GCP.
    """
    if os.environ.get("REGISTER_EXTERNAL_SPECIALISTS", "1") != "1":
        print(">>> Skipping external-agent registration (REGISTER_EXTERNAL_SPECIALISTS=0)")
        return

    print(">>> Registering cross-cloud specialists as Foundry external agents")
    for name, otel_id in EXTERNAL_SPECIALISTS.items():
        try:
            agent = project.agents.create_version(
                agent_name=name,
                definition=ExternalAgentDefinition(otel_agent_id=otel_id),
                description=f"External cross-cloud specialist (otel_agent_id={otel_id}).",
            )
            print(
                f"  + external agent {agent.name} v{agent.version} "
                f"(otel_agent_id={otel_id})"
            )
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: could not register external agent {name}: {e}")


def main() -> int:
    endpoint = _endpoint()
    model = os.environ.get("FOUNDRY_MODEL", "gpt-5.4")
    agent_name = os.environ.get("ROUTING_A2A_AGENT_NAME", "routing-a2a")

    print(f">>> Foundry endpoint: {endpoint}")

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    register_external_specialists(project)

    print(f">>> Registering A2A routing agent: {agent_name} (model {model})")
    tools = build_tools()
    if not tools:
        sys.exit("ERROR: no specialist tools resolved; set the *_AGENT_URL env vars")

    definition = PromptAgentDefinition(
        model=model,
        instructions=INSTRUCTIONS,
        tools=tools,
    )

    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=definition,
        description="A2A multi-cloud routing agent (Seattle/Bengaluru via A2A).",
    )
    print(f">>> Registered: id={agent.id} name={agent.name} version={agent.version}")
    print(f">>> To invoke: agent_reference name={agent.name} version={agent.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

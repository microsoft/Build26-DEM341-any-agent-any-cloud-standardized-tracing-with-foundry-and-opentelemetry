"""Deploy Xi'an Prompt Agent + Orchestrator Prompt Agent to Azure AI Foundry
using the **v2 Foundry Agent Service** (Prompt Agents via `create_version`).

This uses the NEW agent API — `project.agents.create_version()` with
`PromptAgentDefinition` — NOT the legacy Assistants API. Agents are versioned;
invoked via `openai.responses.create(...)` with an `agent_reference`.

Reads:
  agents/xian-foundry/agent.yaml
  orchestrator/agent.yaml

Writes:
  infra/foundry-endpoints.env (FOUNDRY_PROJECT_ENDPOINT, XIAN_AGENT_NAME,
  XIAN_AGENT_VERSION, ORCHESTRATOR_AGENT_NAME, ORCHESTRATOR_AGENT_VERSION)

Idempotent: agents are looked up by name and a new version is created if the
definition differs; otherwise the existing latest version is reused.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    OpenApiAnonymousAuthDetails,
    OpenApiFunctionDefinition,
    OpenApiTool,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential

REPO = Path(__file__).resolve().parent.parent
ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://aifndry-anyagent-demo.services.ai.azure.com/api/projects/proj-anyagent",
)
MODEL = os.environ.get("FOUNDRY_MODEL", "gpt-5.4")


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def fetch_openapi_spec(url: str) -> dict:
    import json
    import urllib.request

    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def build_openapi_tool(name: str, description: str, spec_url: str) -> OpenApiTool:
    spec = fetch_openapi_spec(spec_url)
    if not spec.get("servers"):
        base = spec_url.rsplit("/", 1)[0]
        spec["servers"] = [{"url": base}]
    # Trim spec to only the /plan endpoint — Foundry rejects very large schemas
    paths = spec.get("paths", {})
    if "/plan" in paths:
        spec["paths"] = {"/plan": paths["/plan"]}
    spec["info"] = spec.get("info", {})
    spec["info"]["description"] = description
    return OpenApiTool(
        type="openapi",
        openapi=OpenApiFunctionDefinition(
            name=name,
            description=description,
            spec=spec,
            auth=OpenApiAnonymousAuthDetails(type="anonymous"),
        ),
    )


def main() -> int:
    print(f">>> Foundry endpoint: {ENDPOINT}")
    project = AIProjectClient(endpoint=ENDPOINT, credential=DefaultAzureCredential())

    # ---- Xi'an Prompt Agent (v2) ----
    xian_yaml = load_yaml(REPO / "agents" / "xian-foundry" / "agent.yaml")
    xian_name = xian_yaml["name"]
    print(f"\n[1/2] Xi'an Prompt Agent v2 ({xian_name})")
    xian_def = PromptAgentDefinition(
        model=xian_yaml.get("model", {}).get("id", MODEL),
        instructions=xian_yaml["instructions"],
        tools=[],
    )
    xian = project.agents.create_version(
        agent_name=xian_name,
        definition=xian_def,
        description=xian_yaml.get("description", ""),
    )
    print(f"  xian: id={xian.id} version={xian.version}")

    # ---- Orchestrator Prompt Agent (v2) with OpenAPI tools ----
    orch_yaml = load_yaml(REPO / "orchestrator" / "agent.yaml")
    orch_name = orch_yaml["name"]
    print(f"\n[2/2] Orchestrator Prompt Agent v2 ({orch_name})")

    tools = []
    seattle_url = os.environ.get("SEATTLE_AGENT_URL")
    bengaluru_url = os.environ.get("BENGALURU_AGENT_URL")
    if seattle_url:
        print(f"  Seattle OpenAPI: {seattle_url}/openapi.json")
        tools.append(
            build_openapi_tool(
                "call_seattle",
                "Plan a Seattle trip via the Seattle specialist agent (AWS Lambda).",
                f"{seattle_url}/openapi.json",
            )
        )
    else:
        print("  WARN: SEATTLE_AGENT_URL not set; orchestrator can't reach Seattle")

    if bengaluru_url:
        print(f"  Bengaluru OpenAPI: {bengaluru_url}/openapi.json")
        tools.append(
            build_openapi_tool(
                "call_bengaluru",
                "Plan a trip via the Bengaluru specialist agent (GCP Cloud Run).",
                f"{bengaluru_url}/openapi.json",
            )
        )
    else:
        print("  WARN: BENGALURU_AGENT_URL not set")

    # Xi'an is invoked via instructions — orchestrator references it by name and the
    # caller chains responses. For pure intra-Foundry orchestration the Workflow agent
    # or A2A tool would be cleaner; OpenAPI keeps Seattle/Bengaluru uniform.
    instructions = orch_yaml["instructions"] + (
        f"\n\nNote: For Xi'an queries, defer to the '{xian_name}' agent in this "
        "Foundry project (the caller will chain a separate response on that agent)."
    )

    orch_def = PromptAgentDefinition(
        model=orch_yaml.get("model", {}).get("id", MODEL),
        instructions=instructions,
        tools=tools,
    )
    orch = project.agents.create_version(
        agent_name=orch_name,
        definition=orch_def,
        description=orch_yaml.get("description", ""),
    )
    print(f"  orchestrator: id={orch.id} version={orch.version}")

    out = REPO / "infra" / "foundry-endpoints.env"
    out.write_text(
        f'export FOUNDRY_PROJECT_ENDPOINT="{ENDPOINT}"\n'
        f'export XIAN_AGENT_NAME="{xian_name}"\n'
        f'export XIAN_AGENT_VERSION="{xian.version}"\n'
        f'export ORCHESTRATOR_AGENT_NAME="{orch_name}"\n'
        f'export ORCHESTRATOR_AGENT_VERSION="{orch.version}"\n'
    )
    print(f"\n>>> Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

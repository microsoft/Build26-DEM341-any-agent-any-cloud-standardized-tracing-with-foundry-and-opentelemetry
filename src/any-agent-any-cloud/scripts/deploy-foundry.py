"""Register Foundry prompt/external agents for the DEM341 demo.

This script creates:
- Xi'an as a native Foundry Prompt Agent.
- Seattle as a Foundry Prompt Agent wrapper around an AWS Lambda Function URL.
- Bangalore as a Foundry Prompt Agent wrapper around a GCP Cloud Run URL.

The hosted orchestrator is deployed separately with `azd deploy
foundry-orchestrator`; it calls the external AWS/GCP endpoints directly so
trace context propagates across clouds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    OpenApiAgentTool,
    OpenApiAnonymousAuthDetails,
    OpenApiFunctionDefinition,
    PromptAgentDefinition,
)
from azure.identity import DefaultAzureCredential

REPO = Path(__file__).resolve().parent.parent


def _azd_value(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value:
        return value
    result = subprocess.run(
        ["azd", "env", "get-value", key],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else default


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _external_agent_tool(
    *,
    name: str,
    title: str,
    description: str,
    server_url: str,
    shared_secret: str,
) -> OpenApiAgentTool:
    spec: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": "1.0.0",
            "description": description,
        },
        "servers": [{"url": server_url.rstrip("/")}],
        "paths": {
            "/plan": {
                "post": {
                    "operationId": name,
                    "summary": description,
                    "description": description,
                    "parameters": [
                        {
                            "name": "x-demo-auth",
                            "in": "header",
                            "required": True,
                            "schema": {
                                "type": "string",
                                "default": shared_secret,
                            },
                            "description": "Required demo shared-secret header.",
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "query": {
                                            "type": "string",
                                            "description": "Travel-planning request.",
                                        },
                                        "trip_dates": {
                                            "type": "string",
                                            "nullable": True,
                                        },
                                        "preferences": {
                                            "type": "string",
                                            "nullable": True,
                                        },
                                    },
                                    "required": ["query"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Travel plan response.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "city": {"type": "string"},
                                            "itinerary": {"type": "string"},
                                            "agent": {"type": "string"},
                                            "trace_id": {"type": "string"},
                                        },
                                        "required": [
                                            "city",
                                            "itinerary",
                                            "agent",
                                            "trace_id",
                                        ],
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    return OpenApiAgentTool(
        openapi=OpenApiFunctionDefinition(
            name=name,
            description=description,
            spec=spec,
            auth=OpenApiAnonymousAuthDetails(),
            default_params=["x-demo-auth"],
        )
    )


def _create_external_prompt_agent(
    *,
    project: AIProjectClient,
    model: str,
    agent_name: str,
    city: str,
    operation_name: str,
    server_url: str,
    shared_secret: str,
    hosting_description: str,
) -> str:
    tool = _external_agent_tool(
        name=operation_name,
        title=f"{city} External Agent",
        description=f"Plan a {city} trip via the {city} specialist running on {hosting_description}.",
        server_url=server_url,
        shared_secret=shared_secret,
    )
    instructions = f"""You are a wrapper for the {city} travel specialist.
For every {city} travel request, call {operation_name} exactly once.
Use the default x-demo-auth header parameter from the tool definition.
Return the itinerary field from the tool response as concise Markdown.
"""
    agent = project.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=model,
            instructions=instructions,
            tools=[tool],
        ),
        description=f"{city} external agent backed by {hosting_description}.",
    )
    print(f"{agent_name}: {agent.id} version={agent.version}")
    return str(agent.version)


def main() -> int:
    endpoint = _azd_value("AZURE_AI_PROJECT_ENDPOINT") or _azd_value(
        "FOUNDRY_PROJECT_ENDPOINT"
    )
    model = _azd_value("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5.4")
    shared_secret = _azd_value("DEMO_SHARED_SECRET", "devsecret") or "devsecret"
    seattle_url = _azd_value("SEATTLE_AGENT_URL")
    bangalore_url = _azd_value("BANGALORE_AGENT_URL") or _azd_value("KL_AGENT_URL")

    if not endpoint:
        raise SystemExit("Missing AZURE_AI_PROJECT_ENDPOINT/FOUNDRY_PROJECT_ENDPOINT.")
    if not bangalore_url:
        raise SystemExit("Missing BANGALORE_AGENT_URL. Deploy GCP first.")

    print(f">>> Foundry endpoint: {endpoint}")
    print(f">>> Model deployment: {model}")
    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    xian_yaml = _load_yaml(REPO / "agents" / "xian-foundry" / "agent.yaml")
    xian_name = xian_yaml["name"]
    xian = project.agents.create_version(
        agent_name=xian_name,
        definition=PromptAgentDefinition(
            model=xian_yaml.get("model", {}).get("id", model),
            instructions=xian_yaml["instructions"],
            tools=[],
        ),
        description=xian_yaml.get("description", "Xi'an travel specialist agent"),
    )
    print(f"{xian_name}: {xian.id} version={xian.version}")

    versions: dict[str, str] = {
        "FOUNDRY_PROJECT_ENDPOINT": endpoint,
        "XIAN_AGENT_NAME": xian_name,
        "XIAN_AGENT_VERSION": str(xian.version),
    }

    versions["BANGALORE_AGENT_NAME"] = "bangalore-specialist"
    versions["BANGALORE_AGENT_VERSION"] = _create_external_prompt_agent(
        project=project,
        model=model,
        agent_name="bangalore-specialist",
        city="Bangalore",
        operation_name="call_bangalore_specialist",
        server_url=bangalore_url,
        shared_secret=shared_secret,
        hosting_description="GCP Cloud Run",
    )

    if seattle_url:
        versions["SEATTLE_AGENT_NAME"] = "seattle-specialist"
        versions["SEATTLE_AGENT_VERSION"] = _create_external_prompt_agent(
            project=project,
            model=model,
            agent_name="seattle-specialist",
            city="Seattle",
            operation_name="call_seattle_specialist",
            server_url=seattle_url,
            shared_secret=shared_secret,
            hosting_description="AWS Lambda",
        )
    else:
        print("WARN: SEATTLE_AGENT_URL not set; skipping Seattle wrapper.")

    out = REPO / "infra" / "foundry-endpoints.env"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        "\n".join(f'export {key}="{value}"' for key, value in versions.items()) + "\n",
        encoding="utf-8",
    )
    print(f">>> Wrote {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

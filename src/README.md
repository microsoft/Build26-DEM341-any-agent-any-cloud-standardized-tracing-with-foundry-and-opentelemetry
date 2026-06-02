# Source code

This folder contains the DEM341 "Any Agent, Any Cloud" sample implementation.
It is organized so you can inspect each agent independently or run the full
multi-agent demo stack.

## Layout

| Path | Description |
|:-----|:------------|
| [`agents/`](agents/) | Specialist agents: Seattle on LangGraph/AWS, Bengaluru on Google ADK/GCP, and Xi'an on Azure. |
| [`orchestrator/`](orchestrator/) | Microsoft Agent Framework orchestrator hosted on Microsoft Foundry. |
| [`orchestrator-deepagents/`](orchestrator-deepagents/) | Alternate DeepAgents orchestrator used to compare trace shapes. |
| [`orchestrator-openai-agents/`](orchestrator-openai-agents/) | Alternate OpenAI Agents SDK orchestrator used to compare trace shapes. |
| [`ui/`](ui/) | Next.js trace demo UI. |
| [`scripts/`](scripts/) | Deployment, registration, and evaluation helper scripts. |
| [`azd-infra/`](azd-infra/) | Azure deployment infrastructure. |

## Notes

- Deployment scripts read secrets from environment variables; do not commit
  `.env` files or resolved credentials.
- The demo script/talk track is intentionally not included in this public source
  folder.

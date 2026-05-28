# Source code

The demo implementation lives in [`any-agent-any-cloud/`](any-agent-any-cloud/).

It includes:

- `orchestrator/` — Microsoft Foundry hosted agent using Microsoft Agent Framework.
- `agents/seattle-langgraph/` — Seattle specialist running on AWS Lambda with LangGraph and the Microsoft OpenTelemetry distro for Python.
- `agents/bengaluru-adk/` — Bengaluru specialist running on GCP Cloud Run with Google ADK.
- `agents/xian-foundry/` — Xi'an native Foundry Prompt Agent definition.
- `azd-infra/`, `azure.yaml`, and `scripts/` — deployment assets used by the runbook.

Start with [`../docs/recreate-demo.md`](../docs/recreate-demo.md).

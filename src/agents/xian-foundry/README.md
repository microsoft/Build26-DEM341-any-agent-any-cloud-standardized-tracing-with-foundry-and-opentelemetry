# Xi'an Foundry Prompt Agent

This agent is **declarative** — defined in Azure AI Foundry as a **Prompt Agent**.
No standalone server is needed; the orchestrator invokes it via the Foundry SDK
using its agent ID.

## Definition

See `agent.yaml` for the prompt agent definition. Deploy with the Foundry CLI
(see `../../scripts/deploy-foundry.sh`).

## Trace attributes

Foundry's built-in tracing emits OTel GenAI spans automatically when the
project's linked Application Insights is set to `appi-anyagent-demo`. We
augment with the following resource attributes via the agent's deployment
config:
- `service.name=xian-foundry-prompt`
- `cloud.provider=azure`, `cloud.region=eastus2`
- `gen_ai.agent.name=xian-specialist`
- `demo.city=Xi'an`

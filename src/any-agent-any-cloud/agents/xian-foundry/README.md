# Xi'an Foundry Prompt Agent

This agent is **declarative** — defined in Microsoft Foundry as a **Prompt Agent**.
No standalone server is needed; the orchestrator invokes it via the Foundry SDK
using its agent ID.

## Definition

See `agent.yaml` for the prompt agent definition. Deploy with
`../../scripts/deploy-foundry.py`.

## Trace attributes

Foundry's built-in tracing emits OTel GenAI spans automatically when the
project has a linked Application Insights resource.
- `service.name=xian-foundry-prompt`
- `cloud.provider=azure`, `cloud.region=eastus2`
- `gen_ai.agent.name=xian-specialist`
- `demo.city=Xi'an`

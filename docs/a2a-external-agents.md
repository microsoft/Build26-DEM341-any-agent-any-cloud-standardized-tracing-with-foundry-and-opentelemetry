# A2A external-agent variant

This is an **additive** variant of the demo (branch `a2a-external-agents`) that
exposes the GCP (Bengaluru) and AWS (Seattle) specialists over the
[Agent-to-Agent (A2A) protocol](https://google.github.io/A2A/) and registers
them in Microsoft Foundry as **external agents** via `A2ATool`. The original
HTTP `/plan` orchestrator story is untouched and keeps working.

## Architecture

```
routing-a2a (Foundry prompt agent)
  ├─ A2ATool ─► https://<seattle-fn-url>/a2a        (AWS Lambda, A2A JSON-RPC)
  └─ A2ATool ─► https://<bengaluru-run-url>/a2a     (GCP Cloud Run, A2A JSON-RPC)
```

Each specialist now serves, alongside its existing `POST /plan`:

| Path                              | Purpose                                  |
| --------------------------------- | ---------------------------------------- |
| `/.well-known/agent-card.json`    | A2A agent card (capabilities + skills)   |
| `/a2a`                            | A2A JSON-RPC endpoint (`message/send`)   |

Foundry fetches the card from `<base_url>/.well-known/agent-card.json`, reads its
`url` field, and POSTs A2A messages there. The card's `url` must therefore be the
externally reachable `<public-base>/a2a`, which the deploy scripts set via the
`A2A_PUBLIC_BASE_URL` env var.

## Trace propagation

The A2A client (Foundry) injects W3C `traceparent` on the JSON-RPC POST. The
`_SpecialistExecutor` in `a2a_support.py` extracts it from
`context.call_context.state['headers']` and `otel_context.attach(extract(...))`
before running the plan, so each specialist's spans nest under the caller's
`invoke_agent` span — mirroring the `/plan` path.

## Components

- `agents/seattle-langgraph/a2a_support.py` and
  `agents/bengaluru-adk/a2a_support.py` — identical shared bridge
  (`_SpecialistExecutor`, `build_agent_card`, `mount_a2a`). Duplicated because
  the two agents build/deploy from separate Docker contexts.
- `agents/*/main.py` — append `_a2a_plan()` + `mount_a2a(app, ...)` after the
  existing routes. Bengaluru wraps its plan in an explicit
  `invoke_agent bengaluru_specialist` SERVER span; Seattle relies on the
  LangGraph distro auto-span.
- `agents/*/requirements.txt` — add `a2a-sdk[http-server]>=0.3.7,<0.4`.
- `agents/*/Dockerfile*` — `COPY` now includes `a2a_support.py`.
- `scripts/register-a2a-agents.py` — registers the `routing-a2a` Foundry prompt
  agent with one `A2APreviewTool` per specialist, and registers each specialist
  as a Foundry **external agent** (`ExternalAgentDefinition`).

## Foundry SDK findings (azure-ai-projects 2.2.0)

0. **SDK surface (2.2.0).** `A2ATool` was renamed to `A2APreviewTool` (2.0.1),
   and `ExternalAgentDefinition` (2.2.0) registers a third-party agent for
   observability/eval over customer-emitted OTel data. `register-a2a-agents.py`
   uses both; the repo pins `azure-ai-projects>=2.2.0`.
1. **Card validation is lazy.** `client.agents.create_version` does **not** fetch
   or validate the A2A agent card — that happens at invoke time. The routing
   agent registers successfully even before the specialists serve A2A.
2. **External-agent registration is metadata-only.**
   `ExternalAgentDefinition(otel_agent_id=...)` records the specialist so its own
   OpenTelemetry spans — matched by `gen_ai.agent.id == otel_agent_id` — light up
   in the Foundry trace and evaluation experiences. It does not make the agent
   callable from Foundry; the A2A tool handles invocation.
3. **`azure_ai_agent` connected tool is not invokable here.**
   `AzureAIAgentTarget` (to reuse the Xi'an Foundry prompt agent as a connected
   agent) registers fine, but the responses API rejects it at invoke time:
   `Invalid value: 'azure_ai_agent'`. Xi'an therefore stays in the original
   orchestrator story; the connected-agent path is opt-in via
   `ROUTING_A2A_INCLUDE_XIAN=1` for experimentation only.
4. **A2A tools register without a connection.** The specialist A2A endpoints are
   public (Cloud Run `--allow-unauthenticated`, Lambda Function URL
   `auth-type NONE`), so `A2APreviewTool.project_connection_id` is left unset.

## Deploy / validate (requires refreshed AWS + GCP creds)

> **Blocked at time of writing:** AWS creds expired (`InvalidClientTokenId`) and
> the active GCP project mismatched the demo project, so the specialists could
> not be redeployed with the A2A surface. The steps below complete the loop once
> credentials are refreshed.

```bash
# 1. Redeploy specialists WITH the A2A surface (sets A2A_PUBLIC_BASE_URL).
AZURE_OPENAI_ENDPOINT=... AZURE_AI_MODEL_DEPLOYMENT_NAME=... scripts/deploy-aws.sh
GOOGLE_CLOUD_PROJECT=langgraph-agent-488906 scripts/deploy-gcp.sh

# 2. Confirm each card serves the correct public url.
curl -s "$SEATTLE_AGENT_URL/.well-known/agent-card.json" | python3 -m json.tool
curl -s "$BENGALURU_AGENT_URL/.well-known/agent-card.json" | python3 -m json.tool

# 3. (Re)register the routing agent against the live endpoints.
AZURE_AI_PROJECT_ENDPOINT=... \
SEATTLE_AGENT_URL=... BENGALURU_AGENT_URL=... \
python3 scripts/register-a2a-agents.py

# 4. Invoke routing-a2a and confirm A2A spans nest under the router.
```

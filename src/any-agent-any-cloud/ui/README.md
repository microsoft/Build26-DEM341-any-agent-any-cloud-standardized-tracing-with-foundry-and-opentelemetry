# Any Agent, Any Cloud UI

Next.js chat UI for the Foundry-hosted `foundry-orchestrator` agent. The browser talks to the local `/api/chat` proxy, and the proxy uses `DefaultAzureCredential` to call the Foundry OpenAI Responses stream.

## Prerequisites

- Node.js 20+
- Azure identity available to `DefaultAzureCredential` (`az login`, managed identity, or service principal env vars)

## Required environment variables

Create `.env.local` in `ui/` with:

```bash
FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
FOUNDRY_ORCHESTRATOR_AGENT_VERSION="<active hosted orchestrator version>"
NEXT_PUBLIC_APPLICATIONINSIGHTS_RESOURCE_ID="/subscriptions/<subscription-id>/resourceGroups/<rg>/providers/Microsoft.Insights/components/<app-insights-name>"
```

Optional:

```bash
FOUNDRY_ORCHESTRATOR_AGENT_NAME="foundry-orchestrator"
FOUNDRY_RESPONSE_API_VERSION="2025-11-15-preview"
```

If you authenticate with a service principal instead of `az login`, also set the standard Azure Identity variables:

```bash
AZURE_TENANT_ID="..."
AZURE_CLIENT_ID="..."
AZURE_CLIENT_SECRET="..."
```

## Install and run

```bash
npm install
npm run dev
```

Then open `http://localhost:3000`.

## Notes

- The live flow graph is driven by SSE events from `/api/chat`.
- The proxy detects the routed specialist from a `[city:<name>]` prefix emitted by the orchestrator and strips that marker before forwarding tokens to the browser.
- `NEXT_PUBLIC_ORCH_URL` is no longer needed for local development.

# Any Agent, Any Cloud

This repo is a travel-concierge demo that proves a simple thesis: **Azure AI Foundry Observability works with agents built in any framework, running on any cloud, as long as they emit OpenTelemetry GenAI semantic-convention spans**. In this demo, a Foundry-hosted orchestrator routes requests across Azure, AWS, GCP, and a GitHub Copilot SDK fallback, while everything lands in one Application Insights resource and lights up in Foundry.

**What you'll learn**
- How to route between heterogeneous agents without standardizing on one framework
- How to use OTel GenAI semantic conventions as the interoperability contract
- How `microsoft-opentelemetry` gets Python agents into Azure Monitor quickly
- How an `otelcol-contrib` sidecar bridges OTLP-only runtimes into App Insights
- How W3C `traceparent` propagation stitches one end-to-end trace across clouds

> Reference deployment: `foundry-orchestrator:v17`

## Architecture

```text
Client / curl / Foundry playground
        |
        | OpenAI Responses API
        v
+---------------------------------------------------------------+
| Foundry hosted orchestrator                                   |
| - Microsoft Agent Framework WorkflowBuilder                   |
| - conditional single-target routing                           |
| - deployed by azd as `foundry-orchestrator`                   |
+---------+----------------+----------------+-------------------+
          |                |                |
          | HTTPS          | HTTPS          | Foundry call      | local subprocess
          v                v                v                   v
+------------------+ +------------------+ +------------------+ +------------------+
| Seattle agent    | | Bangalore agent  | | Xi'an agent      | | Copilot fallback |
| LangGraph        | | Google ADK       | | Prompt Agent v2  | | Copilot SDK      |
| AWS Lambda       | | GCP Cloud Run    | | Azure AI Foundry | | in orchestrator  |
| Foundry gpt-5.4  | | Gemini/Vertex AI | | gpt-5.4          | | any 4th city     |
+------------------+ +------------------+ +------------------+ +------------------+
          \                |                /                  /
           \               |               /                  /
            +--------------+--------------+------------------+
                                   |
                                   | OTel GenAI spans + W3C trace context
                                   v
                 +----------------------------------------------+
                 | Application Insights (single resource)       |
                 | -> surfaced in Azure AI Foundry Observability |
                 +----------------------------------------------+
```

## What “any agent, any cloud” means in this repo

The orchestrator is the control plane: a Foundry hosted agent implemented in `orchestrator/main.py` and deployed through `azure.yaml` with `host: azure.ai.agent` and `docker.remoteBuild: true`.

The four execution targets behind that router are:

| Agent | Framework | Cloud / runtime | Model | Source path | Deploy |
|---|---|---|---|---|---|
| Seattle specialist | LangGraph | AWS Lambda | Foundry `gpt-5.4` | `agents/seattle-langgraph/` | `./scripts/deploy-aws.sh` |
| Bangalore specialist | Google ADK | GCP Cloud Run | Gemini on Vertex AI | `agents/bangalore-adk/` | `./scripts/deploy-gcp.sh` |
| Xi'an specialist | Foundry Prompt Agent v2 | Azure AI Foundry | `gpt-5.4` | `agents/xian-foundry/` | `python scripts/deploy-foundry.py` |
| Copilot fallback | GitHub Copilot SDK subprocess | Runs inside the orchestrator container | GitHub Copilot model selection | `orchestrator/main.py` | included in `azd up` / `azd deploy foundry-orchestrator` |

## Why this works

1. **OTel GenAI semantic conventions are the contract.** Foundry Observability does not require every remote agent to use the same framework; it requires spans shaped like standard `gen_ai.*` telemetry.
2. **Python agents use `microsoft-opentelemetry`.** The Seattle, Bangalore, and orchestrator Python code paths use the Microsoft distro to emit GenAI spans and send them to a single Application Insights resource.
3. **Foreign runtimes can still join the trace.** The Copilot SDK emits OTLP only, so the orchestrator container starts an `otelcol-contrib` sidecar (`orchestrator/start.sh`, `orchestrator/otel-collector-config.yaml`) that forwards OTLP to Azure Monitor.
4. **W3C trace propagation stitches the graph.** The orchestrator injects `traceparent` on outbound hops, so AWS, GCP, Azure Foundry, and the local subprocess all show up as one distributed trace.

For the exact span/resource conventions used by this demo, see [`docs/otel-conventions.md`](docs/otel-conventions.md).

## Repo layout

```text
agents/        City specialists: Seattle (LangGraph), Bangalore (ADK), Xi'an (Foundry)
azd-infra/     Bicep for Azure resources provisioned by `azd up`
docs/          OTel conventions and evaluation setup notes
infra/         Generated environment files and connection-string artifacts
logs/          Local log output used during development/demo runs
orchestrator/  Foundry hosted orchestrator, Docker assets, and OTel collector config
scripts/       Deployment helpers for AWS, GCP, and Foundry prompt agents
azure.yaml     azd service definition for the Foundry hosted orchestrator
```

## Quickstart

### Prerequisites

- Azure Developer CLI (`azd`) and Azure CLI authenticated to a subscription
- Python **3.12+**
- Docker (used by the AWS Lambda image build and Azure remote build flow)
- AWS CLI configured for an account that can deploy Lambda, ECR, and IAM resources
- `gcloud` CLI configured for a project that can deploy Cloud Run and Vertex AI access
- A GitHub personal access token usable by the Copilot SDK fallback

### 1) Provision Azure and deploy the Foundry orchestrator

From the repo root:

```bash
azd up
```

This provisions the Foundry project, Application Insights, and deploys the hosted orchestrator defined in `azure.yaml`.

If you only need to redeploy the orchestrator container later:

```bash
azd deploy foundry-orchestrator
```

### 2) Deploy the Seattle specialist to AWS

```bash
./scripts/deploy-aws.sh
```

This builds the Lambda container image, pushes it to ECR, updates the function, and writes `infra/seattle-aws.env`.

### 3) Deploy the Bangalore specialist to GCP

```bash
./scripts/deploy-gcp.sh
```

This deploys from source to Cloud Run and writes `infra/bangalore-gcp.env`.

### 4) Register Foundry prompt and external agents

```bash
python scripts/deploy-foundry.py
```

This creates or updates the Xi'an Prompt Agent plus the Seattle and Bangalore external-agent wrappers, then writes Foundry agent metadata to `infra/foundry-endpoints.env`.

## How to verify it works

1. Invoke the hosted orchestrator with a query such as:

```text
Plan a rainy weekend in Seattle with two kids.
```

2. Confirm the orchestrator routes to the Seattle specialist and returns an itinerary.
3. Open the trace in **Foundry Observability** or query Application Insights and look for:
   - one distributed trace rooted in the hosted orchestrator
   - child spans for the selected specialist call, with W3C trace continuity across hops
   - `gen_ai.*` attributes on the model/tool spans
   - data arriving through the shared Application Insights resource used by all agents
4. Repeat with Xi'an or a non-covered city (for the Copilot fallback) to confirm the same trace model holds across Azure-native and non-Azure runtimes.

Across the full demo, you should see the same observability pipeline cover the orchestrator plus the AWS, GCP, Foundry, and Copilot-backed execution paths.

## Demo guide

For the attendee-facing setup guide, see [`../../docs/recreate-demo.md`](../../docs/recreate-demo.md).

## OTel conventions

For the exact telemetry contract, required attributes, and propagation details, see [`docs/otel-conventions.md`](docs/otel-conventions.md).

## Validation snapshot

The Seattle path was validated in Application Insights with one W3C trace that stitched:

```text
Foundry hosted orchestrator -> AWS Lambda / FastAPI -> LangGraph -> Foundry gpt-5.4
```

The Microsoft OpenTelemetry distro for Python emits the LangGraph spans with `gen_ai.agent.name=seattle_specialist` and `gen_ai.agent.id=seattle-specialist-aws`.

## License / contact

- **License:** no `LICENSE` file is checked in yet; treat the repo as all rights reserved until a license is added.
- **Contact:** open an issue or discussion in this repository for questions, feedback, or follow-up requests.

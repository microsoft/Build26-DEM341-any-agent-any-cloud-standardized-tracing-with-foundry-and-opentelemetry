# Recreate the DEM341 demo

This runbook recreates the "Any agent, any cloud" demo. The final topology is:

- Microsoft Foundry hosted orchestrator using Microsoft Agent Framework.
- Seattle specialist on AWS Lambda using LangGraph and Azure Foundry model calls.
- Bengaluru specialist on GCP Cloud Run using Google ADK and Gemini Flash-Lite.
- Xi'an specialist as a native Foundry Prompt Agent.
- One Application Insights resource collecting distributed traces from all paths.

The scripts assume you are working from `src/any-agent-any-cloud`.

## 1. Prerequisites

Install and authenticate these tools:

- Azure CLI
- Azure Developer CLI (`azd`)
- AWS CLI
- Google Cloud CLI
- Docker with Buildx
- Python 3.12 or later

Authenticate:

```bash
az login
azd auth login

aws configure sso
aws sso login --profile dem341
export AWS_PROFILE=dem341
export AWS_REGION=us-west-2

gcloud auth login
gcloud auth application-default login
gcloud config set project <your-gcp-project-id>
export GOOGLE_CLOUD_PROJECT=<your-gcp-project-id>
```

## 2. Provision Microsoft Foundry and Application Insights

From `src/any-agent-any-cloud`:

```bash
azd env new dem341
azd env set AZURE_LOCATION centralus
azd env set AZURE_AI_DEPLOYMENTS_LOCATION northcentralus
azd env set ENABLE_HOSTED_AGENTS true
azd env set ENABLE_MONITORING true
azd env set DEMO_SHARED_SECRET devsecret
azd provision --no-prompt
```

This provisions:

- Resource group.
- Microsoft Foundry account and project.
- Application Insights and Log Analytics.
- Azure Container Registry for hosted-agent images.
- `gpt-5.4` model deployment used by the orchestrator and Seattle Lambda.

Save the Application Insights connection string for the external agents:

```bash
mkdir -p infra
azd env get-value APPLICATIONINSIGHTS_CONNECTION_STRING > infra/appinsights-conn.txt
```

## 3. Deploy Bengaluru to GCP Cloud Run

The Bengaluru specialist uses Google ADK and defaults to `gemini-2.5-flash-lite` in `us-central1`.

```bash
export GOOGLE_CLOUD_REGION=us-central1
export GOOGLE_CLOUD_LOCATION=us-central1
export VERTEX_MODEL_ID=gemini-2.5-flash-lite
./scripts/deploy-gcp.sh
source infra/bengaluru-gcp.env
azd env set BENGALURU_AGENT_URL "$BENGALURU_AGENT_URL"
```

The script creates or updates:

- Cloud Run service `anyagent-bengaluru`.
- Service account `bengaluru-vertex-sa`.
- Required Google Cloud APIs.
- `infra/bengaluru-gcp.env`.

## 4. Deploy Seattle to AWS Lambda

The Seattle specialist runs on AWS Lambda with LangGraph. To keep the demo independent from Bedrock quota, it calls the Foundry `gpt-5.4` deployment for model work.

For a demo environment, the simplest path is to use an Azure OpenAI key in the Lambda environment. Enable local auth only if your organization allows it:

```bash
RG=$(azd env get-value AZURE_RESOURCE_GROUP)
ACCOUNT=$(azd env get-value AZURE_AI_ACCOUNT_NAME)
ACCOUNT_ID=$(az cognitiveservices account show -g "$RG" -n "$ACCOUNT" --query id -o tsv)

az resource update --ids "$ACCOUNT_ID" --set properties.disableLocalAuth=false

export AZURE_OPENAI_ENDPOINT=$(azd env get-value AZURE_OPENAI_ENDPOINT)
export AZURE_AI_MODEL_DEPLOYMENT_NAME=$(azd env get-value AZURE_AI_MODEL_DEPLOYMENT_NAME)
export AZURE_OPENAI_API_VERSION=2025-04-01-preview
export AZURE_OPENAI_API_KEY=$(az cognitiveservices account keys list -g "$RG" -n "$ACCOUNT" --query key1 -o tsv)

./scripts/deploy-aws.sh
source infra/seattle-aws.env
azd env set SEATTLE_AGENT_URL "$SEATTLE_AGENT_URL"
```

The script creates or updates:

- ECR repository `anyagent/seattle`.
- Lambda function `anyagent-seattle`.
- Public Lambda Function URL.
- `infra/seattle-aws.env`.

For production, prefer Microsoft Entra ID service principal or workload identity federation instead of key-based auth.

## 5. Register Foundry agents

Register the native Xi'an prompt agent and the AWS/GCP external-agent wrappers:

```bash
python scripts/deploy-foundry.py
source infra/foundry-endpoints.env

azd env set XIAN_AGENT_NAME "$XIAN_AGENT_NAME"
azd env set XIAN_AGENT_VERSION "$XIAN_AGENT_VERSION"
azd env set BENGALURU_AGENT_NAME "$BENGALURU_AGENT_NAME"
azd env set BENGALURU_AGENT_VERSION "$BENGALURU_AGENT_VERSION"
azd env set SEATTLE_AGENT_NAME "$SEATTLE_AGENT_NAME"
azd env set SEATTLE_AGENT_VERSION "$SEATTLE_AGENT_VERSION"
```

The external-agent wrappers use OpenAPI tools for:

- `SEATTLE_AGENT_URL/plan`
- `BENGALURU_AGENT_URL/plan`

## 6. Deploy the hosted orchestrator

```bash
azd deploy foundry-orchestrator --no-prompt
```

Hosted-agent deployment can sometimes report a `containers/default:start` 404 even when the new version becomes active. If that happens, check the version status in Foundry or wait a minute and invoke the agent.

## 7. Validate the demo

Invoke the hosted orchestrator:

```bash
ENDPOINT=$(azd env get-value AZURE_AI_PROJECT_ENDPOINT)
TOKEN=$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)

curl -fsS -X POST \
  "$ENDPOINT/agents/foundry-orchestrator/endpoint/protocols/openai/responses?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":"Plan a rainy Seattle coffee stop and one iconic indoor activity. Keep it concise."}'
```

Repeat with:

- `Give me one concise Bengaluru coffee recommendation.`
- `Give me one concise Xi'an history recommendation.`

## 8. Validate traces in Application Insights

Use a fixed trace ID so the trace is easy to find:

```bash
TRACE_ID=ccccddddeeeeffff0000111122223333
SPAN_ID=34567890abcdef12

curl -fsS -X POST \
  "$ENDPOINT/agents/foundry-orchestrator/endpoint/protocols/openai/responses?api-version=2025-05-15-preview" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "traceparent: 00-$TRACE_ID-$SPAN_ID-01" \
  -d '{"input":"Plan a rainy Seattle coffee stop and one iconic indoor activity. Keep it concise."}'
```

Query Application Insights:

```kusto
union isfuzzy=true AppRequests, AppDependencies, AppTraces
| where TimeGenerated > ago(30m)
| where OperationId == "ccccddddeeeeffff0000111122223333"
| project TimeGenerated, OperationId, AppRoleName, Type, Name, Id, ParentId, DurationMs, Success, Properties, Message
| order by TimeGenerated asc
```

For a Seattle route, the important trace shape is:

```text
foundry-orchestrator
├─ router decision
│  └─ invoke_agent city_router
│     └─ chat gpt-5.4
└─ invoke_agent seattle_specialist
   └─ POST /plan                         (AWS Lambda / FastAPI)
      └─ seattle.plan
         └─ invoke_agent seattle_specialist
            └─ invoke_agent LangGraph    (Microsoft OpenTelemetry distro)
               └─ plan
                  └─ seattle.azure_foundry.invoke
                     └─ POST /openai/responses
```

The LangGraph spans are emitted by the Microsoft OpenTelemetry distro for Python with static agent identity configured in `agents/seattle-langgraph/telemetry.py`.

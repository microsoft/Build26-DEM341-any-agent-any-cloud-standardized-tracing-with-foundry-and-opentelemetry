#!/usr/bin/env bash
# Deploy Bangalore agent to GCP Cloud Run (source-based build).
# Scale-to-zero: no fixed cost when idle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${GOOGLE_CLOUD_PROJECT:-langgraph-agent-488906}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
MODEL_ID="${VERTEX_MODEL_ID:-gemini-2.5-flash-lite}"
MODEL_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="anyagent-bangalore"
SA_EMAIL="bangalore-vertex-sa@${PROJECT}.iam.gserviceaccount.com"

CONN_STRING="$(cat "$ROOT/infra/appinsights-conn.txt")"

echo ">>> Ensure required GCP APIs enabled"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com \
  --project="$PROJECT" 2>&1 | tail -3

echo ">>> Ensure Cloud Run service account exists"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create bangalore-vertex-sa \
    --project="$PROJECT" \
    --display-name="Bangalore Vertex AI service account"
fi

echo ">>> Allow SA to pull from Artifact Registry / run as Cloud Run identity"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/aiplatform.user --condition=None 2>&1 | tail -2

# Cloud Run needs to invoke the SA — granted at deployment via --service-account flag.

echo ">>> Deploy from source (Cloud Build builds and pushes image)"
gcloud run deploy "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --source="$ROOT/agents/bangalore-adk" \
  --service-account="$SA_EMAIL" \
  --allow-unauthenticated \
  --port=8080 \
  --cpu=1 --memory=1Gi --timeout=120s \
  --min-instances=0 --max-instances=3 \
  --set-env-vars="APPLICATIONINSIGHTS_CONNECTION_STRING=${CONN_STRING},GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_REGION=${REGION},GOOGLE_CLOUD_LOCATION=${MODEL_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=true,VERTEX_MODEL_ID=${MODEL_ID},DEMO_SHARED_SECRET=devsecret"

URL="$(gcloud run services describe "$SERVICE" --project="$PROJECT" --region="$REGION" --format='value(status.url)')"
URL="${URL%/}"
{
  echo "BANGALORE_AGENT_URL=$URL"
  echo "KL_AGENT_URL=$URL"
} | tee "$ROOT/infra/bangalore-gcp.env"
echo ">>> done."

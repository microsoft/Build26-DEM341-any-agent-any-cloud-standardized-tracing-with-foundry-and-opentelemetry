#!/usr/bin/env bash
# Deploy Seattle agent to AWS Lambda (container image) + Function URL.
# Scale-to-zero: no fixed cost when idle.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
NAME="anyagent-seattle"
REPO="anyagent/seattle"
ROLE_NAME="anyagent-seattle-lambda-role"
IMAGE_TAG="latest"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"
ARCH="arm64"

CONN_STRING="$(cat "$ROOT/infra/appinsights-conn.txt")"
: "${AZURE_OPENAI_ENDPOINT:?AZURE_OPENAI_ENDPOINT is required}"
: "${AZURE_AI_MODEL_DEPLOYMENT_NAME:?AZURE_AI_MODEL_DEPLOYMENT_NAME is required}"
if [[ -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
  : "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required when AZURE_OPENAI_API_KEY is not set}"
  : "${AZURE_CLIENT_ID:?AZURE_CLIENT_ID is required when AZURE_OPENAI_API_KEY is not set}"
  : "${AZURE_CLIENT_SECRET:?AZURE_CLIENT_SECRET is required when AZURE_OPENAI_API_KEY is not set}"
fi
AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-04-01-preview}"

echo ">>> [1/7] Ensure ECR repo $REPO in $REGION"
aws ecr describe-repositories --region "$REGION" --repository-names "$REPO" >/dev/null 2>&1 \
  || aws ecr create-repository --region "$REGION" --repository-name "$REPO" --image-scanning-configuration scanOnPush=true >/dev/null

echo ">>> [2/7] Build & push image ($ARCH)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null
docker buildx build --platform "linux/${ARCH}" \
  --provenance=false --sbom=false \
  -f "$ROOT/agents/seattle-langgraph/Dockerfile.lambda" \
  -t "${ECR_URI}:${IMAGE_TAG}" \
  --push \
  "$ROOT/agents/seattle-langgraph"

echo ">>> [3/7] Ensure IAM role $ROLE_NAME"
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" >/dev/null
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null
fi

echo ">>> [4/7] Lambda execution role only needs CloudWatch Logs; LLM calls use Azure Foundry"

ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

echo ">>> [5/7] Wait for IAM role to propagate (10s)"
sleep 10

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
export CONN_STRING REGION AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_VERSION AZURE_AI_MODEL_DEPLOYMENT_NAME
export AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY:-}"
export AZURE_TENANT_ID="${AZURE_TENANT_ID:-}"
export AZURE_CLIENT_ID="${AZURE_CLIENT_ID:-}"
export AZURE_CLIENT_SECRET="${AZURE_CLIENT_SECRET:-}"
python3 - <<'PY' > "$ENV_FILE"
import json
import os
import sys

variables = {
    "APPLICATIONINSIGHTS_CONNECTION_STRING": os.environ["CONN_STRING"],
    "AWS_REGION_OVERRIDE": os.environ["REGION"],
    "AZURE_OPENAI_ENDPOINT": os.environ["AZURE_OPENAI_ENDPOINT"],
    "AZURE_OPENAI_API_VERSION": os.environ["AZURE_OPENAI_API_VERSION"],
    "AZURE_AI_MODEL_DEPLOYMENT_NAME": os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
    "DEMO_SHARED_SECRET": "devsecret",
    "AWS_LWA_INVOKE_MODE": "buffered",
}
if os.environ.get("AZURE_OPENAI_API_KEY"):
    variables["AZURE_OPENAI_API_KEY"] = os.environ["AZURE_OPENAI_API_KEY"]
else:
    variables["AZURE_TENANT_ID"] = os.environ["AZURE_TENANT_ID"]
    variables["AZURE_CLIENT_ID"] = os.environ["AZURE_CLIENT_ID"]
    variables["AZURE_CLIENT_SECRET"] = os.environ["AZURE_CLIENT_SECRET"]

json.dump(
    {"Variables": variables},
    fp=sys.stdout,
)
PY

if aws lambda get-function --region "$REGION" --function-name "$NAME" >/dev/null 2>&1; then
  echo ">>> [6/7] Update existing Lambda image"
  aws lambda update-function-code --region "$REGION" --function-name "$NAME" --image-uri "${ECR_URI}:${IMAGE_TAG}" >/dev/null
  aws lambda wait function-updated --region "$REGION" --function-name "$NAME"
  aws lambda update-function-configuration --region "$REGION" --function-name "$NAME" \
    --timeout 120 --memory-size 1024 --environment "file://$ENV_FILE" >/dev/null
else
  echo ">>> [6/7] Create Lambda from image"
  aws lambda create-function --region "$REGION" --function-name "$NAME" \
    --package-type Image --code ImageUri="${ECR_URI}:${IMAGE_TAG}" \
    --role "$ROLE_ARN" --timeout 120 --memory-size 1024 \
    --architectures "$ARCH" \
    --environment "file://$ENV_FILE" >/dev/null
  aws lambda wait function-active-v2 --region "$REGION" --function-name "$NAME"
fi

echo ">>> [7/7] Ensure public Function URL"
URL="$(aws lambda get-function-url-config --region "$REGION" --function-name "$NAME" --query FunctionUrl --output text 2>/dev/null || true)"
if [[ -z "$URL" ]]; then
  URL="$(aws lambda create-function-url-config --region "$REGION" --function-name "$NAME" --auth-type NONE \
    --cors '{"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"]}' \
    --query FunctionUrl --output text)"
  aws lambda add-permission --region "$REGION" --function-name "$NAME" \
    --statement-id PublicInvoke --action lambda:InvokeFunctionUrl \
    --principal "*" --function-url-auth-type NONE >/dev/null
fi
aws lambda add-permission --region "$REGION" --function-name "$NAME" \
  --statement-id PublicInvokeFunction --action lambda:InvokeFunction \
  --principal "*" >/dev/null 2>&1 || true

URL="${URL%/}"
echo "SEATTLE_AGENT_URL=$URL" | tee "$ROOT/infra/seattle-aws.env"
echo ">>> done."

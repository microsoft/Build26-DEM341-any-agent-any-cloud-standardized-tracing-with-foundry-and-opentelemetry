# Foundry Orchestrator

The orchestrator is deployed as a **Foundry Hosted Agent** (`agent.yaml`)
with workflow executors that call the remote city specialists. For local
development, run `main.py` directly to exercise the same routing and OTel
trace propagation contract before deploying to Foundry.

## Local run (dev shim)

```bash
cd orchestrator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export SEATTLE_LAMBDA_NAME=anyagent-seattle
export SEATTLE_LAMBDA_REGION=us-west-2
# AWS credentials for boto3 (or rely on ~/.aws/credentials)
# export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
export BANGALORE_AGENT_URL=http://localhost:8081
# Xi'an always talks to the remote Foundry Prompt Agent:
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export XIAN_AGENT_NAME=xian-specialist
export APPLICATIONINSIGHTS_CONNECTION_STRING="$(cat ../infra/appinsights-conn.txt)"
export DEMO_SHARED_SECRET=devsecret

uvicorn main:app --host 0.0.0.0 --port 8000
```

Test:
```bash
curl -X POST http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{"query":"Weekend in Seattle, foodie focus"}'
```

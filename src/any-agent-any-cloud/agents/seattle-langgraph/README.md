# Seattle Specialist Agent

Travel specialist for **Seattle**. Stack:
- **LangGraph** (agent framework)
- **Azure Foundry model deployment** (LLM)
- **FastAPI** server exposing `POST /plan`
- **OpenTelemetry GenAI semantic conventions** via the `microsoft-opentelemetry` distro

## Local run

```bash
cd agents/seattle-langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_REGION=<aws-region>
export AZURE_OPENAI_ENDPOINT="https://<account>.openai.azure.com/"
export AZURE_AI_MODEL_DEPLOYMENT_NAME=<model-deployment-name>
export AZURE_OPENAI_API_KEY=...
export APPLICATIONINSIGHTS_CONNECTION_STRING="$(cat ../../infra/appinsights-conn.txt)"
export DEMO_SHARED_SECRET=devsecret

uvicorn main:app --host 0.0.0.0 --port 8080
```

Test:
```bash
curl -X POST http://localhost:8080/plan \
  -H 'content-type: application/json' \
  -H 'x-demo-auth: devsecret' \
  -d '{"query":"3 days in Seattle in October, foodie + outdoors"}'
```

## Trace attributes

- `service.name=seattle-langgraph`
- `cloud.provider=aws`, `cloud.region=<aws-region>`
- `gen_ai.system=az.ai.foundry`, `gen_ai.agent.name=seattle_specialist`
- LangGraph spans include `gen_ai.agent.id=seattle-specialist-aws`
- `demo.city=Seattle`

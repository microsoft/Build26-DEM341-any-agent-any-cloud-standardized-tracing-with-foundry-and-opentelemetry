# Seattle Specialist Agent

Travel specialist for **Seattle**. Stack:
- **LangGraph** (agent framework)
- **Claude 3.5 Sonnet on AWS Bedrock** (LLM)
- **FastAPI** server exposing `POST /plan`
- **OpenTelemetry GenAI semantic conventions** via the `microsoft-opentelemetry` distro

## Local run

```bash
cd agents/seattle-langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_REGION=us-west-2
export AWS_ACCESS_KEY_ID=...           # or use SSO/profile
export AWS_SECRET_ACCESS_KEY=...
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
- `cloud.provider=aws`, `cloud.region=us-west-2`
- `gen_ai.system=aws.bedrock`, `gen_ai.agent.name=seattle-specialist`
- `demo.city=Seattle`

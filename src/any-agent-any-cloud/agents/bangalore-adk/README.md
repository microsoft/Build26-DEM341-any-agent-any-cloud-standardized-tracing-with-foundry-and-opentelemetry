# Bangalore Specialist Agent

Travel specialist for **Bangalore / Bengaluru**. Stack:
- **Google ADK** (Agent Development Kit)
- **Gemini on Vertex AI**
- **FastAPI** server exposing `POST /plan`
- **OpenTelemetry GenAI semconv** via the `microsoft-opentelemetry` distro

## Local run

```bash
cd agents/bangalore-adk
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_CLOUD_PROJECT=langgraph-agent-488906
export GOOGLE_CLOUD_REGION=us-central1
export GOOGLE_CLOUD_LOCATION=us-central1
export VERTEX_MODEL_ID=gemini-2.5-flash-lite
export GOOGLE_GENAI_USE_VERTEXAI=true
gcloud auth application-default login   # one-time
export APPLICATIONINSIGHTS_CONNECTION_STRING="$(cat ../../infra/appinsights-conn.txt)"
export DEMO_SHARED_SECRET=devsecret

uvicorn main:app --host 0.0.0.0 --port 8081
```

Test:
```bash
curl -X POST http://localhost:8081/plan \
  -H 'content-type: application/json' \
  -H 'x-demo-auth: devsecret' \
  -d '{"query":"4 days in Bangalore with kids, food-focused"}'
```

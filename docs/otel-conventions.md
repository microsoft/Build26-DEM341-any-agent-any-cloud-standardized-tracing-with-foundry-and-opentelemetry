# OTel GenAI Conventions for "Any Agent, Any Cloud" Demo

All four agents (Seattle / LangGraph on AWS, Bangalore / ADK on GCP, Xi'an / Foundry Prompt Agent, Foundry Hosted Orchestrator) emit OpenTelemetry traces following the **GenAI semantic conventions** and export to a single Azure Monitor (Application Insights) instance via OTLP.

## Required span attributes

| Attribute | Example | Notes |
|---|---|---|
| `gen_ai.system` | `az.ai.foundry`, `gcp.vertex_ai` | Model provider |
| `gen_ai.request.model` | `gpt-5.4`, `gemini-2.5-flash-lite` | Model id as called |
| `gen_ai.operation.name` | `chat`, `invoke_agent` | Operation kind. The orchestrator emits one `invoke_agent <agent.name>` client span per sub-agent dispatch. |
| `gen_ai.agent.name` | `seattle_specialist`, `bangalore_specialist`, `xian-specialist`, `copilot-fallback` | Stable agent identifier. On an `invoke_agent` span this is the **invoked** specialist, not the caller. |
| `gen_ai.agent.id` | `seattle-specialist-aws` | Optional deployed-agent identifier when a framework instrumentor supports it. The Seattle LangGraph agent passes this into the LangChain instrumentor. |
| `gen_ai.usage.input_tokens` | `523` | Input tokens (when known) |
| `gen_ai.usage.output_tokens` | `812` | Output tokens (when known) |
| `service.name` | `seattle-langgraph` | OTel resource: per-agent service name |
| `service.namespace` | `anyagent-demo` | Shared across all agents |
| `cloud.provider` | `aws` / `gcp` / `azure` | Identifies hosting cloud |
| `cloud.region` | `us-west-2`, `us-central1`, `northcentralus` | |
| `demo.city` | `Seattle`, `Bangalore`, `Xi'an` | Custom: which specialist |

## Span structure

- One **root span** per user request on the orchestrator (`gen_ai.operation.name=agent`, `gen_ai.agent.name=orchestrator`).
- Each call to a sub-agent is a **child client span** named `invoke_agent <agent.name>` with `gen_ai.operation.name=invoke_agent` and `gen_ai.agent.name` set to the invoked specialist (`seattle_specialist`, `bangalore_specialist`, `xian-specialist`, or `copilot-fallback`). The W3C `traceparent` header is injected inside that span so the remote/server-side spans nest under it.
- The sub-agent's FastAPI server (Seattle, Bangalore) creates a **server span** that becomes the parent for its internal LLM calls. The Microsoft OpenTelemetry distro for Python also instruments LangGraph/LangChain in the Seattle path and stamps static `gen_ai.agent.*` identity on those spans.
- For Xi'an, the Foundry OpenAI client's `AIProjectInstrumentor` emits its own auto `invoke_agent xian-specialist` span when `responses.create(extra_body.agent_reference=...)` is called. The orchestrator re-attaches its OTel context inside the worker thread so that auto span is parented under the manual `invoke_agent xian-specialist` span (not a sibling).

## Trace propagation

- W3C Trace Context (`traceparent`, `tracestate`) HTTP headers.
- Orchestrator → sub-agents: inject via OTel `TextMapPropagator` on outbound HTTP.
- Sub-agents: extract on incoming request and set as the active context for the handler.

## Agent service-name registry

| Agent | `service.name` | `gen_ai.agent.name` | `demo.city` |
|---|---|---|---|
| Orchestrator | `foundry-orchestrator` | `orchestrator` | n/a |
| Seattle | `seattle-langgraph` | `seattle_specialist` | `Seattle` |
| Bangalore | `bangalore-adk` | `bangalore_specialist` | `Bangalore` |
| Xi'an | `xian-foundry-prompt` | `xian-specialist` | `Xi'an` |
| Copilot fallback | `foundry-orchestrator` | `copilot-fallback` | n/a |

## Export configuration

All Python agents use the **`microsoft-opentelemetry`** distro ([microsoft/opentelemetry-distro-python](https://github.com/microsoft/opentelemetry-distro-python)), which wraps the Azure Monitor exporter and auto-instruments `agent_framework`, `openai`, `langchain`, `semantic_kernel`, `fastapi`, `httpx`, and `requests` against the OpenTelemetry GenAI semantic conventions:

```
APPLICATIONINSIGHTS_CONNECTION_STRING=<from infra/appinsights-conn.txt>
OTEL_SERVICE_NAME=<per agent>
OTEL_RESOURCE_ATTRIBUTES=service.namespace=anyagent-demo,cloud.provider=<aws|gcp|azure>,cloud.region=<region>
ENABLE_SENSITIVE_DATA=true  # record prompts/tool args/results
```

The Seattle Lambda path uses `microsoft-opentelemetry` for instrumentation and a direct `azure-monitor-opentelemetry-exporter` `SimpleSpanProcessor` so spans flush before the Lambda runtime freezes between invocations.

Foundry-hosted agents and prompt agents inherit Foundry's built-in tracing; ensure the Foundry project has an Application Insights connection before validating traces.

## API contract for sub-agents

```
POST /plan
Headers: traceparent, tracestate, x-demo-auth: <shared HMAC>
Body: { "query": "<user request>", "trip_dates": "optional", "preferences": "optional" }
Response: { "city": "...", "itinerary": "markdown", "agent": "...", "trace_id": "..." }
```

All four agents return `trace_id` so the UI can deep-link to Foundry Observability for that trace.

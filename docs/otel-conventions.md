# OTel GenAI Conventions for "Any Agent, Any Cloud" Demo

The showcase orchestrator and specialists (DeepAgents orchestrator in Foundry, Seattle / LangGraph on AWS, Bengaluru / ADK on GCP, and Xi'an on Azure) emit OpenTelemetry traces following the **GenAI semantic conventions** and export to a single Azure Monitor (Application Insights) instance.

## Required span attributes

| Attribute | Example | Notes |
|---|---|---|
| `gen_ai.system` | `aws.bedrock`, `gcp.vertex_ai`, `az.ai.foundry` | Model provider |
| `gen_ai.request.model` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Model id as called |
| `gen_ai.operation.name` | `chat`, `invoke_agent` | Operation kind. The orchestrator emits one `invoke_agent <agent.name>` client span per sub-agent dispatch. |
| `gen_ai.agent.name` | `deepagents-orchestrator`, `seattle_specialist`, `bengaluru_specialist`, `xian-specialist` | Stable agent identifier. On an `invoke_agent` span this is the visible agent for that span. |
| `gen_ai.agent.id` | `seattle-specialist-aws` | Optional deployed-agent identifier when a framework instrumentor supports it. The Seattle LangGraph agent passes this into the LangChain instrumentor. |
| `gen_ai.usage.input_tokens` | `523` | Input tokens (when known) |
| `gen_ai.usage.output_tokens` | `812` | Output tokens (when known) |
| `gen_ai.input.messages` | `[{"role":"user","parts":[...]}]` | Required on visible `invoke_agent` spans so the trace details panel shows the request. |
| `gen_ai.output.messages` | `[{"role":"assistant","parts":[...]}]` | Required on visible `invoke_agent` spans so the trace details panel shows the response. |
| `gen_ai.message.content` | `Plan 2 days in Seattle...` | Captured on GenAI message events when content tracing is enabled. |
| `service.name` | `seattle-langgraph` | OTel resource: per-agent service name |
| `service.namespace` | `anyagent-demo` | Shared across all agents |
| `cloud.provider` | `aws` / `gcp` / `azure` | Identifies hosting cloud |
| `cloud.region` | `us-west-2`, `asia-south1`, `eastus2` | |
| `demo.city` | `Seattle`, `Bengaluru`, `Xi'an` | Custom: which specialist |

## Span structure

- One **root span** per user request on the DeepAgents orchestrator: `invoke_agent deepagents-orchestrator`, with span-level input and output.
- Each selected city appears as an `execute_tool <city>_plan` span with `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`.
- Each tool span parents a visible `invoke_agent <specialist>` boundary span with `gen_ai.input.messages` and `gen_ai.output.messages`.
- Seattle and Bengaluru emit their own external-agent subtrees with registered OTel IDs: `seattle-specialist-aws` and `bengaluru-specialist-gcp`.
- Xi'an is intentionally hosted as a tracing-light Azure service; the orchestrator-owned `invoke_agent xian-specialist` boundary span carries the input/output used for trace review and evaluation.

## Trace propagation

- W3C Trace Context (`traceparent`, `tracestate`) HTTP headers, with internal `x-demo-traceparent` / `x-demo-tracestate` preservation headers for cloud ingress paths that rewrite the standard parent span id.
- Orchestrator → sub-agents: inject via OTel `TextMapPropagator` on outbound HTTP.
- Sub-agents: extract on incoming request and set as the active context for the handler.

## Agent service-name registry

| Agent | `service.name` | `gen_ai.agent.name` | `demo.city` |
|---|---|---|---|
| Orchestrator | `deepagents-orchestrator` | `deepagents-orchestrator` | n/a |
| Seattle | `seattle-langgraph` | `seattle_specialist` | `Seattle` |
| Bengaluru | `bengaluru-adk` | `bengaluru_specialist` | `Bengaluru` |
| Xi'an | `xian-a2a` | `xian-specialist` | `Xi'an` |

## Export configuration

Python agents use the **`microsoft-opentelemetry`** distro ([microsoft/opentelemetry-distro-python](https://github.com/microsoft/opentelemetry-distro-python)), which wraps the Azure Monitor exporter and auto-instruments supported frameworks, FastAPI, HTTP clients, and model calls against the OpenTelemetry GenAI semantic conventions:

```
APPLICATIONINSIGHTS_CONNECTION_STRING=<from infra/appinsights-conn.txt>
OTEL_SERVICE_NAME=<per agent>
OTEL_RESOURCE_ATTRIBUTES=service.namespace=anyagent-demo,cloud.provider=<aws|gcp|azure>,cloud.region=<region>
ENABLE_SENSITIVE_DATA=true  # record prompts/tool args/results
AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_AND_EVENT
OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental
```

The Azure-Monitor exporter (`azure-monitor-opentelemetry-exporter`) is pulled in transitively by `microsoft-opentelemetry`; we only import it directly to install a `SimpleSpanProcessor` workaround for AWS Lambda freeze-on-return.

The Foundry-hosted DeepAgents orchestrator inherits Foundry's built-in tracing provider, then enables the Microsoft LangChain/GenAI instrumentor so the DeepAgents spans land in the project-linked Application Insights resource.

## API contract for sub-agents

```
POST /plan
Headers: traceparent, tracestate, x-demo-traceparent, x-demo-tracestate, x-demo-auth: <shared HMAC>
Body: { "query": "<user request>", "trip_dates": "optional", "preferences": "optional" }
Response: { "city": "...", "itinerary": "markdown", "agent": "...", "trace_id": "..." }
```

Agents return `trace_id` where the host can observe it directly; Foundry-hosted orchestrator responses are also discoverable from the Foundry trace and evaluation views.

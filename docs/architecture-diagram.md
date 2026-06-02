# Any Agent, Any Cloud — Architecture Diagram

```mermaid
flowchart TB
    user["User / demo app"]
    foundry["Microsoft Foundry project<br/>dem341"]
    appi["Application Insights<br/>shared trace store"]
    model["Foundry model deployment<br/>gpt-5.4"]

    orchestrator["deepagents-orchestrator<br/>Foundry Hosted Agent<br/>DeepAgents<br/>Responses protocol"]
    tools["Parallel city tools<br/>seattle_plan / bengaluru_plan / xian_plan"]

    seattle["Seattle specialist<br/>AWS Lambda<br/>LangGraph + FastAPI<br/>microsoft-opentelemetry"]
    bengaluru["Bengaluru specialist<br/>GCP Cloud Run<br/>Google ADK + FastAPI<br/>microsoft-opentelemetry"]
    xian["Xi'an specialist<br/>Azure Container Apps<br/>Responses model call"]

    user -->|"OpenAI Responses call"| orchestrator
    orchestrator --> tools
    tools -->|"seattle_plan"| seattle
    tools -->|"bengaluru_plan"| bengaluru
    tools -->|"xian_plan"| xian

    seattle -->|"model call"| model
    xian -->|"model call"| model

    seattle -. "W3C traceparent" .-> appi
    bengaluru -. "W3C traceparent" .-> appi
    xian -. "orchestrator boundary span" .-> appi
    orchestrator -. "Foundry hosted-agent tracing" .-> appi
    tools -. "execute_tool spans" .-> appi

    foundry --- orchestrator
    foundry --- model
    foundry --- appi
```

## Trace shape to show on stage

```mermaid
sequenceDiagram
    participant U as User
    participant O as deepagents-orchestrator
    participant T as parallel city tools
    participant S as Seattle / AWS Lambda
    participant B as Bengaluru / GCP Cloud Run
    participant X as Xi'an / Azure Container Apps
    participant AI as Application Insights

    U->>O: Ask about one or more cities
    O->>T: decide matching city tools

    par Seattle branch
        T->>S: execute_tool seattle_plan -> invoke_agent seattle_specialist + traceparent
        S->>AI: POST /plan, seattle.plan, LangGraph, model spans
    and Bengaluru branch
        T->>B: execute_tool bengaluru_plan -> invoke_agent bengaluru_specialist + traceparent
        B->>AI: POST /plan, bengaluru.plan, ADK/Gemini spans
    and Xi'an branch
        T->>X: execute_tool xian_plan -> invoke_agent xian-specialist + traceparent
        O->>AI: visible Xi'an boundary span with input/output
    end

    O-->>U: Combined response with one section per city
    O->>AI: root invoke_agent, execute_tool, specialist dispatch, response storage spans
```

## Key observability contract

- Every cross-agent boundary is represented as an `invoke_agent <agent>` span.
- W3C `traceparent` propagates from the orchestrator to AWS Lambda and GCP Cloud Run.
- Python services use the Microsoft OpenTelemetry distro to emit FastAPI, HTTP, model, and framework spans.
- All telemetry lands in the same Application Insights resource, which lets Foundry Observability show one distributed trace across clouds.

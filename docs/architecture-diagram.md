# Any Agent, Any Cloud — Architecture Diagram

```mermaid
flowchart TB
    user["User / demo app"]
    foundry["Microsoft Foundry project<br/>dem341"]
    appi["Application Insights<br/>shared trace store"]
    model["Foundry model deployment<br/>gpt-5.4"]

    orchestrator["foundry-orchestrator<br/>Foundry Hosted Agent<br/>Microsoft Agent Framework<br/>Responses protocol"]
    router["Router decision<br/>city_router + gpt-5.4"]

    seattle["Seattle specialist<br/>AWS Lambda<br/>LangGraph + FastAPI<br/>microsoft-opentelemetry"]
    bengaluru["Bengaluru specialist<br/>GCP Cloud Run<br/>Google ADK + FastAPI<br/>microsoft-opentelemetry"]
    xian["Xi'an specialist<br/>Foundry Prompt Agent<br/>gpt-5.4"]
    copilot["Fallback<br/>GitHub Copilot SDK<br/>OTLP via collector"]

    user -->|"OpenAI Responses call"| orchestrator
    orchestrator --> router
    router -->|"route: Seattle"| seattle
    router -->|"route: Bengaluru"| bengaluru
    router -->|"route: Xi'an"| xian
    router -->|"unsupported city"| copilot

    seattle -->|"model call"| model
    xian -->|"model call"| model

    seattle -. "W3C traceparent" .-> appi
    bengaluru -. "W3C traceparent" .-> appi
    xian -. "Foundry tracing" .-> appi
    copilot -. "OTLP sidecar" .-> appi
    orchestrator -. "Foundry hosted-agent tracing" .-> appi
    router -. "GenAI spans" .-> appi

    foundry --- orchestrator
    foundry --- xian
    foundry --- model
    foundry --- appi
```

## Trace shape to show on stage

```mermaid
sequenceDiagram
    participant U as User
    participant O as foundry-orchestrator
    participant R as city_router
    participant S as Seattle / AWS Lambda
    participant B as Bengaluru / GCP Cloud Run
    participant X as Xi'an / Foundry Prompt Agent
    participant AI as Application Insights

    U->>O: Ask about one or more cities
    O->>R: invoke_agent city_router
    R-->>O: {"cities":["seattle","bengaluru","xian"]}

    par Seattle branch
        O->>S: invoke_agent seattle_specialist + traceparent
        S->>AI: POST /plan, seattle.plan, LangGraph, model spans
    and Bengaluru branch
        O->>B: invoke_agent bengaluru_specialist + traceparent
        B->>AI: POST /plan, bengaluru.plan, ADK/Gemini spans
    and Xi'an branch
        O->>X: invoke_agent xian-specialist
        X->>AI: invoke_agent xian-specialist:1, chat gpt-5.4
    end

    O-->>U: Combined response with [city:*] sections
    O->>AI: router, specialist dispatch, response storage spans
```

## Key observability contract

- Every cross-agent boundary is represented as an `invoke_agent <agent>` span.
- W3C `traceparent` propagates from the orchestrator to AWS Lambda and GCP Cloud Run.
- Python services use the Microsoft OpenTelemetry distro to emit FastAPI, HTTP, model, and framework spans.
- All telemetry lands in the same Application Insights resource, which lets Foundry Observability show one distributed trace across clouds.

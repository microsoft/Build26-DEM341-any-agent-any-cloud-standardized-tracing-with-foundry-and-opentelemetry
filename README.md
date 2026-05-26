<a name="start-building"></a>
<br>
<p align="center">
<img src="img/banner-build-26.png" alt="Microsoft Build 2026" width="1200"/>
</p>

# [Microsoft Build 2026](https://build.microsoft.com)

## 🔥 DEM341: Any agent, any cloud: Standardized tracing with Foundry + OpenTelemetry

### Session Description

Teams are shipping agents across clouds and frameworks, but telemetry is often fragmented. This demo shows how Microsoft Foundry and OpenTelemetry GenAI semantic conventions create one observability plane for agents running on Microsoft Foundry, AWS Lambda, GCP Cloud Run, and GitHub Copilot SDK-based fallback paths.

The repo contains the reproducible demo assets: a Foundry-hosted orchestrator, a Seattle LangGraph agent on AWS Lambda, a Bangalore Google ADK agent on GCP Cloud Run, a Xi'an Foundry Prompt Agent, deployment scripts, OpenTelemetry conventions, and evaluation setup notes.

### 🚀 Getting started

1. Clone this repository.
1. Open the demo source in [`src/any-agent-any-cloud`](src/any-agent-any-cloud).
1. Follow [`docs/recreate-demo.md`](docs/recreate-demo.md) to provision Microsoft Foundry, deploy the external agents, and validate traces in Application Insights.
1. Review [`docs/otel-conventions.md`](docs/otel-conventions.md) for the span names, resource attributes, and trace propagation contract used by the demo.

### 🧠 Learning Outcomes

By the end of this demo, you will be able to:

- Explain how OpenTelemetry GenAI semantic conventions make agent traces portable across frameworks and clouds.
- Deploy a Foundry-hosted orchestrator that routes to agents running on Microsoft Foundry, AWS Lambda, and GCP Cloud Run.
- Use the Microsoft OpenTelemetry distro for Python to emit request, framework, and model spans into Azure Monitor.
- Validate a distributed trace in Application Insights and Microsoft Foundry Observability.

### 💬 Keep Learning with Copilot

Try these prompts with GitHub Copilot Chat after you clone the repo:

- `Explain how src/any-agent-any-cloud/orchestrator/main.py propagates W3C trace context to the city specialists.`
- `Show me where the Seattle LangGraph agent configures microsoft-opentelemetry and how its spans get agent identity attributes.`
- `Walk me through docs/recreate-demo.md and tell me which cloud credentials I need before running the deployment scripts.`
- `Using docs/otel-conventions.md, summarize the ideal trace tree for a Seattle request.`
- `Help me adapt the Bangalore ADK agent to a different city while preserving the GenAI telemetry attributes.`

### 💻 Technologies Used

1. [Microsoft Foundry](https://learn.microsoft.com/azure/ai-foundry/)
1. [Foundry hosted agents](https://learn.microsoft.com/azure/ai-foundry/agents/concepts/hosted-agents)
1. [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
1. [Microsoft OpenTelemetry distro for Python](https://github.com/microsoft/opentelemetry-distro-python)
1. [Azure Monitor Application Insights](https://learn.microsoft.com/azure/azure-monitor/app/app-insights-overview)
1. [AWS Lambda](https://docs.aws.amazon.com/lambda/)
1. [Google Cloud Run](https://cloud.google.com/run/docs)
1. [Google Agent Development Kit](https://google.github.io/adk-docs/)
1. [LangGraph](https://langchain-ai.github.io/langgraph/)

### 📚 Resources and Next Steps

| Resource | Description |
|:---------|:------------|
| [`docs/recreate-demo.md`](docs/recreate-demo.md) | End-to-end setup and deployment runbook. |
| [`docs/otel-conventions.md`](docs/otel-conventions.md) | Span names, attributes, service names, and propagation rules used by the demo. |
| [`docs/foundry-eval-setup.md`](docs/foundry-eval-setup.md) | Optional evaluation setup for trace-linked quality checks. |
| [`src/any-agent-any-cloud`](src/any-agent-any-cloud) | Source code for the orchestrator, agents, infrastructure, and deployment scripts. |
| [https://aka.ms/build26-next-steps](https://aka.ms/build26-next-steps) | Explore lab and session repos to further your learning from Microsoft Build. |

### 🌟 Microsoft Learn MCP Server

The Microsoft Learn MCP Server gives your AI agent direct access to Microsoft's official documentation — grounded, up-to-date answers about the products and services covered in this session.

**Visual Studio Code** — One click installation:

[![Install in Visual Studio Code](https://img.shields.io/badge/Visual_Studio_Code-Install_Microsoft_Learn_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=microsoft-learn&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Flearn.microsoft.com%2Fapi%2Fmcp%22%7D)

**GitHub Copilot CLI** — Run this to install the Learn MCP Server as a plugin:

```text
/plugin install microsoftdocs/mcp
```

For more information, other clients, and to post questions, visit the [Learn MCP Server repo](https://aka.ms/learnmcp).

## Content Owners

<table>
<tr>
    <td align="center"><a href="https://github.com/nagkumar91">
        <img src="https://github.com/nagkumar91.png" width="100px;" alt="Nagkumar"/><br />
        <sub><b>Nagkumar</b></sub></a><br />
            <a href="https://github.com/nagkumar91" title="talk">📢</a>
    </td>
</tr></table>

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a CLA and decorate the pull request appropriately. Simply follow the instructions provided by the bot. You will only need to do this once across all repositories using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general). Any use of third-party trademarks or logos are subject to those third-party's policies.

<a name="start-building"></a>
<br>
<p align="center">
<img src="img/banner-build-26.png" alt="Microsoft Build 2026" width="1200"/>
</p>

# [Microsoft Build 2026](https://build.microsoft.com)

## DEM341: Any agent, any cloud: Standardized tracing with Foundry and OpenTelemetry

### Overview

Teams are shipping agents across clouds and frameworks, but telemetry is fragmented. In this demo, see how Microsoft Foundry and OpenTelemetry GenAI semantic conventions bring consistent observability to agents built with different frameworks and hosted on different clouds. We will walk through a multi-agent travel concierge, diagnose routing and latency with unified traces, and close the loop with trace-based evaluation in Microsoft Foundry.

This repository is the central starting point for DEM341. Start here for the demo walkthrough, sample code, slides, and community links.

### Getting started

If you are following the demo at your own pace:

1. Clone this repository.
2. Review the session map below to find the docs, samples, slides, and community links.
3. Open the demo walkthrough and sample code when they are published in this repo.
4. Use GitHub Issues for questions, bugs, or follow-up requests.

### Demo showcase

The demo proves a simple thesis: Foundry observability can work with any agent framework on any cloud when every agent emits standard OpenTelemetry GenAI spans.

| Demo component | What it shows |
|:---------------|:--------------|
| Foundry hosted orchestrator | A bring-your-own-code agent hosted by Microsoft Foundry, implemented with Microsoft Agent Framework and the OpenAI Responses protocol. |
| Seattle specialist | A LangGraph travel agent running on AWS Lambda. |
| Bengaluru specialist | A Google Agent Development Kit travel agent running on Google Cloud Run. |
| Xi'an specialist | A Microsoft Foundry Prompt Agent using a low-code/no-code authoring flow. |
| GitHub Copilot fallback | A fallback path for unsupported cities so the app still returns a useful answer. |
| Unified tracing | One distributed trace across Foundry, AWS, Google Cloud, and GitHub-backed execution paths using W3C trace context and OpenTelemetry GenAI semantic conventions. |
| Trace-based evaluation | Evaluation results that connect quality, routing, latency, and trace evidence in Microsoft Foundry. |

### Learning outcomes

By the end of this demo, you will be able to:

1. Explain how OpenTelemetry GenAI semantic conventions make agent observability framework-agnostic and cloud-agnostic.
2. Route a user request from a Foundry hosted orchestrator to agents running in Microsoft Foundry, AWS, Google Cloud, and a GitHub Copilot fallback.
3. Propagate W3C trace context across agent boundaries so a single request appears as one distributed trace.
4. Use Microsoft Foundry Observability and evaluation to debug agent routing, latency, cost, and answer quality.

### Explore with GitHub Copilot

Try these prompts with GitHub Copilot to explore the topics from this demo. Open Copilot Chat in Visual Studio Code (`Ctrl+Alt+I` on Windows/Linux, `Cmd+Shift+I` on Mac), paste a prompt, and use this repository as the working context.

Use these as a starting point — or write your own!

- "Explain how the orchestrator routes a request to the Seattle, Bengaluru, Xi'an, or fallback agent."
- "Show me where W3C trace context is injected before calling a remote specialist agent."
- "Summarize the OpenTelemetry GenAI attributes this demo relies on and where each one is emitted."
- "Find the evaluation dataset and explain which rows test happy paths, fan-out, fallback, and adversarial behavior."

### Technologies used

| Area | Technologies |
|:-----|:-------------|
| Agent hosting and orchestration | Microsoft Foundry hosted agents, Microsoft Agent Framework, OpenAI Responses protocol |
| Agent frameworks | LangGraph, Google Agent Development Kit, Microsoft Foundry Prompt Agent |
| Cloud runtimes | Microsoft Azure, AWS Lambda, Google Cloud Run |
| Observability | OpenTelemetry, OpenTelemetry GenAI semantic conventions, W3C Trace Context, Application Insights, Microsoft Foundry Observability |
| Evaluation | Microsoft Foundry evaluation, curated travel-planning prompts |
| Demo app | Next.js, React Flow, GitHub Copilot SDK fallback |

### Resources and next steps

Everything for DEM341 should be discoverable from this repository.

| Resource | Description |
|:---------|:------------|
| [README.md](README.md) | Session overview and the starting point for all DEM341 material. |
| [docs/](docs/) | Demo walkthroughs, architecture notes, OpenTelemetry conventions, and evaluation setup. |
| [src/](src/) | Demo source and samples, including the orchestrator, specialist agents, user interface, and deployment helpers. |
| Slides | Session slides will be linked from this README after the deck is cleared for publication. |
| [GitHub Issues](../../issues) | Ask questions, report problems, or request follow-up examples. |
| [SUPPORT.md](SUPPORT.md) | Support expectations and Microsoft open source support information. |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Community participation guidelines. |

## Content Owners

<table>
<tr>
    <td align="center"><a href="https://github.com/luigiw">
        <img src="https://github.com/luigiw.png" width="100px;" alt="Hanchi Wang"/><br />
        <sub><b>Hanchi Wang</b></sub></a><br />
            <a href="https://github.com/luigiw" title="GitHub profile">GitHub profile</a>
    </td>
    <td align="center"><a href="https://github.com/nagkumar91">
        <img src="https://github.com/nagkumar91.png" width="100px;" alt="Nagkumar Arkalgud"/><br />
        <sub><b>Nagkumar Arkalgud</b></sub></a><br />
            <a href="https://github.com/nagkumar91" title="GitHub profile">GitHub profile</a>
    </td>
</tr></table>

## Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.

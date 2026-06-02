# Foundry trace evaluation setup

This sample uses Microsoft Foundry trace-based evaluations over the traces emitted by the showcase `deepagents-orchestrator` and the external city specialists.

## Recommended evaluators

Use query/response evaluators that match the trace data available on every visible agent span:

- Intent Resolution
- Task Adherence
- Task Completion

Avoid mapping `tool_calls` or `tool_definitions` unless you have confirmed those fields are present in the converted trace rows for the specific agent. For the demo traces, the most reliable SDK mapping is:

```text
query={{item.query}}
response={{item.response}}
```

## Useful trace targets

| Target | How to select rows |
|:-------|:-------------------|
| DeepAgents orchestrator | Use recent trace IDs containing `gen_ai.agent.name=deepagents-orchestrator`. |
| Seattle external agent | Use direct Seattle trace IDs or rows with `gen_ai.agent.id=seattle-specialist-aws`. |
| Bengaluru external agent | Use direct Bengaluru trace IDs or rows with `gen_ai.agent.id=bengaluru-specialist-gcp`. |
| Xi'an boundary | Use orchestrator traces containing the visible `invoke_agent xian-specialist` boundary span. The Xi'an Azure service is intentionally tracing-light. |

## SDK pattern

The Azure AI Projects SDK trace-eval flow is:

1. Create an eval with `data_source_config={"type":"azure_ai_source","scenario":"traces"}`.
2. Add `TestingCriterionAzureAIEvaluator` criteria for the three evaluators above.
3. Create a run with `data_source={"type":"azure_ai_traces","trace_ids":[...]}`.
4. Poll until the run reaches `completed`.
5. Inspect `client.evals.runs.output_items.list(...)` for per-row agent IDs, scores, pass/fail status, and reasons.

Use direct city-agent trace IDs when you want eval output rows to show only the external specialists. Using a distributed trace ID that includes the orchestrator will also include sibling orchestrator rows in the converted dataset.

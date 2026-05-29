# Foundry evaluation setup

This guide keeps the DEM341 evaluation setup simple. Use your deployed orchestrator, the Application Insights resource linked to your Foundry project, and a judge model available in that project.

## Prerequisites

- A Microsoft Foundry project with the orchestrator deployed.
- Application Insights linked to the Foundry project.
- Access to the agent Monitor and Evaluation experiences in Foundry.
- The curated dataset in `scripts/eval-dataset.jsonl`.

## Continuous evaluation

Use continuous evaluation when you want Foundry to score live orchestrator traffic.

1. Open your Foundry project.
2. Go to **Build -> Agents** and open the orchestrator agent.
3. Open **Monitor** and then **Continuous evaluation** settings.
4. Turn continuous evaluation on.
5. Start with a small sample rate and a conservative hourly cap.
6. Add evaluators that match the demo story:
   - **Intent Resolution** for routing.
   - **Task Adherence** for staying on task.
   - **Tool Call Accuracy** for specialist selection when tool-call data is available.
   - **Response Completeness** only when you provide ground-truth answers.
7. Save the rule and send a few fresh requests through the orchestrator.

## Offline evaluation

Use offline evaluation when you want a repeatable dataset-driven check.

1. In Foundry, go to **Evaluation -> Create**.
2. Choose the orchestrator agent as the target.
3. Upload `scripts/eval-dataset.jsonl`.
4. Map the user query field to the agent input.
5. Keep `expected_city`, `intent`, and `notes` as review columns.
6. Choose the same evaluator family used for continuous evaluation.
7. Run the evaluation and inspect aggregate results plus row-level explanations.

## Local helper

The repo also includes `scripts/run-offline-eval.py` for a lightweight local pass over the same dataset. It calls the deployed orchestrator, records responses and routing metadata, and writes a CSV for review.

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export FOUNDRY_ORCHESTRATOR_AGENT="foundry-orchestrator"

python scripts/run-offline-eval.py \
  --dataset scripts/eval-dataset.jsonl \
  --output scripts/offline-eval-results.csv
```

## Trace review

After sending traffic, open the orchestrator in Foundry Monitor and drill into a trace. For a supported-city request, the trace should show:

- a root orchestrator span
- a child span for the routed city specialist
- model and tool spans with OpenTelemetry GenAI attributes
- one end-to-end trace across the orchestrator and specialist runtime

## Demo flow

1. Send a supported-city request.
2. Show the orchestrator response.
3. Open the trace from Monitor.
4. Point out the routed specialist child span.
5. Show how evaluation connects response quality back to trace evidence.

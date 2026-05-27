# Foundry continuous evaluation setup for `foundry-orchestrator`

This runbook wires Microsoft Foundry evaluation onto the **Any Agent, Any Cloud** orchestrator traces in your Microsoft Foundry project.

## Environment snapshot

- Foundry project: your deployed project
- AI Services account: your deployed Foundry account
- Hosted agent: `foundry-orchestrator` (latest observed version: `v17`)
- Linked Application Insights: the Application Insights resource provisioned by `azd`
- Judge model deployment to reuse: `gpt-5.4`
- Portal entry point: open the project in Foundry, then go to the `foundry-orchestrator` agent page (the `foundry-orchestrator` agent page in the Foundry portal)

## 1) Prerequisites

### Access and RBAC

Minimum access to complete the full setup:

- **Foundry project Owner or Contributor** on your Foundry project
- **Azure AI User** on the Foundry project if rule creation or evaluation submission is blocked
- **Application Insights access** on your Application Insights resource
  - Reader is enough for dashboard viewing
  - Contributor helps if you need to confirm linked-resource settings
- **Log Analytics Reader** on the Application Insights workspace if you want to run KQL

If continuous-eval rule creation fails in the portal, the most common fix is assigning the **project managed identity** the **Azure AI User** role on the Foundry project.

### Model deployment

Use the existing judge deployment:

- Deployment name: **`gpt-5.4`**
- Region: same project region
- Use it for AI-assisted evaluators

### Trace prerequisites

This repo already emits the right trace shape:

- Top-level orchestrator span: **`chat foundry-orchestrator`**
- Child specialist spans: **`seattle.specialist`**, **`bangalore.specialist`**, **`xian.specialist`**, or **`chat github-copilot`**
- Application Insights destination: your linked Application Insights resource

The code contract behind the traces is in:

- `orchestrator/agent.yaml`
- `orchestrator/main.py`
- `agents/seattle-langgraph/main.py`
- `agents/bangalore-adk/main.py`

## 2) Online evaluation (continuous, on production traces)

> **Portal-only:** this section is done in the Foundry UI.

### Recommended starting configuration

Use this as the first production setting:

- Sampling rate: **10%**
- Max hourly evaluation runs: **100/hour**
- Judge model: **`gpt-5.4`**
- Results destination: your linked Application Insights resource

### Click path

1. Open **Microsoft Foundry** and enter your project.
2. Go to **Build -> Agents**.
3. Open **`foundry-orchestrator`**.
4. Select the **Monitor** tab.
5. Click the **gear / Settings** icon.
6. Open **Continuous evaluation**.
7. Turn **Continuous evaluation** on.
8. Confirm the telemetry source is the project-linked **Application Insights** resource.
9. Add evaluators:
   - **Intent Resolution**
   - **Task Adherence**
   - **Tool Call Accuracy**
   - **Response Completeness** **if it is offered in your portal build**
10. Set **sample rate = 10%**.
11. Set or confirm **max hourly runs = 100**.
12. Save.

### Important caveat for this orchestrator

Two evaluator caveats matter for this demo:

1. **Response Completeness** usually expects a reference/ground-truth answer. If the portal does **not** offer it for continuous trace evaluation, use:
   - **Task Completion** online, and
   - keep **Response Completeness** for offline batch runs where you provide ground truth.
2. **Tool Call Accuracy** is the right evaluator to try first, but this orchestrator dispatches to specialists through workflow/external calls. Validate on a few known-good traces. If the portal says there is not enough structured tool-call data, keep **Intent Resolution** and **Task Adherence** online and use manual/App Insights review for routing correctness.

### Verify it is working

1. Generate 5-10 fresh production requests through the hosted orchestrator endpoint or the agent playground.
2. Return to **Monitor** on `foundry-orchestrator`.
3. Expand the time range to **Last 30 minutes**.
4. Verify you see:
   - token usage
   - latency
   - run success rate
   - evaluation charts for the enabled evaluators
5. Open a trace and confirm the span tree includes:
   - `chat foundry-orchestrator`
   - the routed specialist span (`seattle.specialist`, `bangalore.specialist`, `xian.specialist`, or `chat github-copilot`)

## 3) Offline evaluation (batch, curated dataset)

The curated dataset for this repo is:

- `scripts/eval-dataset.jsonl`

Each row contains:

```json
{"query":"...","expected_city":"seattle|bangalore|xian|copilot","intent":"trip_planning|...","notes":"..."}
```

Use it two ways:

- **Portal batch evaluation** for judge-scored metrics
- **Local helper script** for a lightweight CSV of actual responses, latency, trace IDs, and routed city

### 3A. Run an offline evaluation in the Foundry portal

> **Portal-only:** this section is done in the Foundry UI.

1. In **Foundry**, open your project.
2. Go to **Evaluation -> Create**.
3. For **Target**, choose **Agent**.
4. Select agent **`foundry-orchestrator`** (latest / `v17` unless you are intentionally pinning another version).
5. Upload `scripts/eval-dataset.jsonl` as the dataset.
6. In evaluator selection, start with:
   - **Intent Resolution**
   - **Task Adherence**
   - **Tool Call Accuracy**
7. Choose **`gpt-5.4`** as the judge deployment when prompted.
8. Review field mapping:
   - map `query` -> query/input
   - keep `expected_city`, `intent`, and `notes` as reference columns for row review
9. Submit the evaluation.
10. When the run completes, open the result page and inspect aggregate scores plus row-level results.

### 3B. If you want Response Completeness offline

The provided dataset is intentionally lightweight for routing evaluation. To run **Response Completeness** in the portal, add a ground-truth style column first, for example:

- `ground_truth`
- or `expected_response`

Then map:

- `query` -> query
- generated agent answer -> response
- `ground_truth` / `expected_response` -> ground truth

Without a ground-truth field, treat `expected_city` + `notes` as manual review aids rather than direct inputs to a completeness evaluator.

### 3C. Run the helper script (local, no portal)

The repo includes `scripts/run-offline-eval.py`.

It:

- reads `scripts/eval-dataset.jsonl`
- calls the deployed orchestrator through the Foundry Responses API
- captures the returned text, `trace_id`, latency, and routed city
- writes a CSV of raw results
- prints a small summary table

Environment variables:

```bash
export FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export FOUNDRY_ORCHESTRATOR_AGENT="foundry-orchestrator"
# optional:
export FOUNDRY_ORCHESTRATOR_VERSION="v17"
```

Example command:

```bash
python scripts/run-offline-eval.py \
  --dataset scripts/eval-dataset.jsonl \
  --output scripts/offline-eval-results.csv
```

> Do **not** run this casually during the demo prep window unless you actually want fresh live traffic and token spend.

## 4) Where results show up

### Foundry portal

**Continuous evaluation / production traces**

- `foundry-orchestrator` -> **Monitor** tab
- evaluation charts on the agent monitoring dashboard
- trace-linked drill-down from the Monitor experience

**Offline/batch evaluation**

- project-level **Evaluation** page
- agent-level **Evaluation** tab
- evaluation run detail page with aggregate scores, tokens, and row-level results

### Application Insights

Foundry stores monitoring data in the linked Application Insights resource.

Start with these KQL queries.

**Latest orchestrator traces**

```kusto
traces
| where customDimensions["service.name"] == "foundry-orchestrator"
| order by timestamp desc
| take 50
```

**Evaluation result records (most reliable query pattern)**

```kusto
traces
| where message == "gen_ai.evaluation.result"
| order by timestamp desc
| project timestamp, message, customDimensions
```

**If your workspace maps evaluation data into custom events**

```kusto
customEvents
| where name has "evaluation" or tostring(customDimensions) has "gen_ai.evaluation"
| order by timestamp desc
| project timestamp, name, customDimensions
```

## 5) Screenshots to capture for the demo

Capture these before showtime so you have both live and backup visuals:

1. **Agent page -> Monitor tab**
   - call out **Latency**, **Run success rate**, and evaluation score trend lines
2. **Monitor settings panel -> Continuous evaluation**
   - show enabled evaluators and the **10%** sample rate
3. **Single trace view**
   - highlight `chat foundry-orchestrator`
   - highlight one specialist child span (`seattle.specialist`, `bangalore.specialist`, `xian.specialist`, or `chat github-copilot`)
4. **Project -> Evaluation run list**
   - call out **Status**, **Evaluation tokens**, **Target tokens**, and aggregate scores
5. **Evaluation row detail**
   - show one supported-city case and one fallback case
   - call out the routed city and score explanation

### Suggested metric callouts

Use realistic thresholds rather than promising exact values:

- **Run success rate:** aim for **>95%**
- **Intent Resolution:** call out anything consistently **high/pass** across Seattle/Bangalore/Xi'an/fallback paths
- **Task Adherence:** point out cases where the agent stayed in travel-planning scope
- **Latency:** compare supported-city routes vs fallback (`chat github-copilot`) route
- **Tool Call Accuracy:** use as a diagnostic metric; if sparse, explain that this orchestrator uses cross-cloud external specialists rather than only first-party tools

## 6) Cost notes

Continuous evaluation burns judge-model tokens in addition to normal app traffic.

A good back-of-envelope estimate for the starter setup (**10% sampling**, **4 evaluators**) is:

- assume **~1,000 judged tokens per evaluator per sampled run**
- total **~4,000 judge tokens per sampled run**

Example:

- production traffic = **1,000 requests/day**
- 10% sampling = **100 evaluated requests/day**
- 100 x 4,000 judge tokens -> **~400,000 judge tokens/day**
- monthly order of magnitude -> **~12M judge tokens/month**

Use that token estimate with your current **`gpt-5.4`** pricing to get the actual dollar amount. If you temporarily raise sampling to **25%** for rehearsal/demo day, multiply the estimate by **2.5x**.

## 7) Troubleshooting

### Missing traces

Symptoms:

- Monitor charts are empty
- no new traces under `foundry-orchestrator`
- trace tree missing child specialist spans

Checks:

1. Confirm the expected Application Insights resource is still linked to your Foundry project.
2. Confirm you are looking at the correct time range in Foundry.
3. Wait 2-5 minutes for ingestion.
4. Generate a fresh request and search for the returned `trace_id`.
5. Verify the orchestrator still emits the expected root span pattern (`chat foundry-orchestrator`).
6. If Seattle/Bangalore child spans are missing, confirm upstream services are still forwarding W3C trace context.

### Judge model / evaluator errors

Symptoms:

- evaluation run fails immediately
- evaluator shows initialization/configuration error
- portal refuses to save the rule

Checks:

1. Confirm the **`gpt-5.4`** deployment exists and is healthy.
2. Confirm the evaluator you selected is supported for the evaluation mode:
   - **Response Completeness** generally needs ground truth
   - **Tool Call Accuracy** can be limited if the trace lacks structured tool-call payloads
3. Check model quota/capacity in the region where the model is deployed.
4. Reduce sampling or max hourly runs if you hit capacity.

### RBAC / permission errors

Symptoms:

- cannot open Monitor or Evaluation pages
- KQL queries fail
- continuous-eval rule creation is denied

Checks:

1. Foundry project role: **Owner** or **Contributor**
2. Project role for evaluation operations: **Azure AI User**
3. App Insights role: **Reader** or better
4. Log Analytics role for KQL: **Log Analytics Reader**
5. If rule creation fails, assign the **Foundry project managed identity** the **Azure AI User** role

### Evaluation runs skipped

If Monitor shows skipped evaluation runs, the usual cause is the hourly cap. Increase the hourly limit or wait for the next hour bucket.

## 8) Recommended demo flow

1. Turn on continuous evaluation for `foundry-orchestrator`.
2. Send 3-5 live prompts covering Seattle, Bangalore, Xi'an, and fallback.
3. Show the **Monitor** tab first.
4. Drill into one trace.
5. Then show the offline batch evaluation run from `scripts/eval-dataset.jsonl` for the curated regression view.

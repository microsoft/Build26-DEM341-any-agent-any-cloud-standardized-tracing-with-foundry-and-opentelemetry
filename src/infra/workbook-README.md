# Any Agent, Any Cloud workbook

## What this workbook shows

- Time-range and `cloud_RoleName` filters (multi-select).
- Hourly trace volume by role.
- Latency p50/p95/p99 by role.
- Token totals by role and by model.
- Top 10 slowest GenAI traces, with a deep link into App Insights transaction detail by `operation_Id`.
- Best-effort cost estimate using editable static rates.
- Failure rate per role.

## Files

- Workbook JSON: `/Users/hanchiwang/Code/DEM341/infra/workbook-anyagent.json`
- This guide: `/Users/hanchiwang/Code/DEM341/infra/workbook-README.md`

## Deploy / import

### Portal import (recommended)

1. Open **Application Insights** resource **`appi-anyagent-demo`**.
2. Go to **Workbooks** > **New**.
3. Choose **Advanced Editor**.
4. Paste the contents of `infra/workbook-anyagent.json`.
5. Select **Apply**, then **Done Editing**, then **Save**.

This workbook JSON is a gallery-style workbook definition (`Notebook/1.0`) and is intended to be pasted directly into the Advanced Editor.

### CLI / ARM wrapper option

If you want IaC deployment instead of a manual paste, wrap the workbook JSON as `templateData` inside a `microsoft.insights/workbooktemplates` ARM template, then deploy it with `az deployment group create`. Microsoft’s sample shape is documented here:

- https://learn.microsoft.com/en-us/azure/azure-monitor/visualize/workbooks-samples

At a high level:

1. Read `infra/workbook-anyagent.json`.
2. Put that JSON under `resources[].properties.templateData` in a workbook template ARM file.
3. Deploy the ARM file with `az deployment group create -g rg-anyagent-demo -f <template>.json`.

Because the Advanced Editor import is the simplest path, this repo does **not** include the extra ARM wrapper file.

## Edit the cost rates

Open the **Cost estimate (best-effort)** tile in the workbook editor and update the `datatable(...)` at the top of the query:

```kusto
let rates = datatable(gen_ai_system:string, input_rate_per_1k_usd:real, output_rate_per_1k_usd:real, rate_note:string)
[
  'az.ai.foundry', 0.0050, 0.0150, 'Edit for your Foundry / Azure OpenAI model price',
  'openai', 0.0050, 0.0150, 'Edit for your OpenAI model price',
  'aws.bedrock', 0.0030, 0.0150, 'Edit for your Bedrock model price',
  'google.vertex_ai', 0.00125, 0.0050, 'Edit for your Gemini / Vertex AI model price',
  'github-copilot', 0.0000, 0.0000, 'Placeholder only; set your internal chargeback rate',
  'unknown', 0.0000, 0.0000, 'Fallback placeholder'
];
```

Adjust the prices to match the models and pricing meters you actually use.

## KQL used by the workbook

### Role filter parameter

```kusto
union isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system' or cloud_RoleName startswith 'anyagent-demo.' or cloud_RoleName in ('copilot', 'github-copilot')
| where isnotempty(cloud_RoleName)
| summarize by cloud_RoleName
| order by cloud_RoleName asc
```

### Trace volume by role

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| summarize trace_count = count() by bin(timestamp, 1h), cloud_RoleName
| order by timestamp asc
| render columnchart with (kind=stacked, xcolumn=timestamp, series=cloud_RoleName, ycolumns=trace_count, title='Trace volume by cloud_RoleName')
```

### Latency p50/p95/p99 by role

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| summarize
    spans = count(),
    p50_ms = round(percentile(duration_ms, 50), 1),
    p95_ms = round(percentile(duration_ms, 95), 1),
    p99_ms = round(percentile(duration_ms, 99), 1)
  by cloud_RoleName
| order by p95_ms desc
```

### Tokens by role

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| summarize
    input_tokens = sum(input_tokens),
    output_tokens = sum(output_tokens),
    total_tokens = sum(total_tokens)
  by cloud_RoleName
| order by total_tokens desc
| render barchart with (xcolumn=cloud_RoleName, ycolumns=total_tokens, title='Total input + output tokens by role')
```

### Tokens by model

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| summarize
    input_tokens = sum(input_tokens),
    output_tokens = sum(output_tokens),
    total_tokens = sum(total_tokens)
  by model
| top 20 by total_tokens desc
| render barchart with (xcolumn=model, ycolumns=total_tokens, title='Tokens by model')
```

### Top 10 slowest traces

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| where isnotempty(operation_Id)
| project
    timestamp,
    cloud_RoleName,
    item_kind = iff(TableName == 'requests', 'request', 'dependency'),
    trace_name = coalesce(operation_Name, name),
    gen_ai_system,
    gen_ai_operation,
    model,
    duration_ms = round(duration_ms, 1),
    success,
    operation_Id,
    portal_link = strcat('https://portal.azure.com/#blade/AppInsightsExtension/transactionDetail/operationId/', operation_Id, '/resourceId/%2Fsubscriptions%2Fb17253fa-f327-42d6-9686-f3e553e24763%2FresourceGroups%2Frg-anyagent-demo%2Fproviders%2Fmicrosoft.insights%2Fcomponents%2Fappi-anyagent-demo')
| top 10 by duration_ms desc
```

### Cost estimate

```kusto
let selected_roles = dynamic([{RoleFilter}]);
let rates = datatable(gen_ai_system:string, input_rate_per_1k_usd:real, output_rate_per_1k_usd:real, rate_note:string)
[
  'az.ai.foundry', 0.0050, 0.0150, 'Edit for your Foundry / Azure OpenAI model price',
  'openai', 0.0050, 0.0150, 'Edit for your OpenAI model price',
  'aws.bedrock', 0.0030, 0.0150, 'Edit for your Bedrock model price',
  'google.vertex_ai', 0.00125, 0.0050, 'Edit for your Gemini / Vertex AI model price',
  'github-copilot', 0.0000, 0.0000, 'Placeholder only; set your internal chargeback rate',
  'unknown', 0.0000, 0.0000, 'Fallback placeholder'
];
union isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| summarize
    input_tokens = sum(input_tokens),
    output_tokens = sum(output_tokens)
  by gen_ai_system, model
| lookup kind=leftouter rates on gen_ai_system
| extend input_rate_per_1k_usd = coalesce(input_rate_per_1k_usd, 0.0)
| extend output_rate_per_1k_usd = coalesce(output_rate_per_1k_usd, 0.0)
| extend estimated_input_usd = round((todouble(input_tokens) / 1000.0) * input_rate_per_1k_usd, 4)
| extend estimated_output_usd = round((todouble(output_tokens) / 1000.0) * output_rate_per_1k_usd, 4)
| extend estimated_total_usd = round(estimated_input_usd + estimated_output_usd, 4)
| order by estimated_total_usd desc
```

### Failure rate per role

```kusto
let selected_roles = dynamic([{RoleFilter}]);
union withsource=TableName isfuzzy=true dependencies, requests
| where timestamp {TimeRange}
| where customDimensions has 'gen_ai.system'
| where isnotempty(cloud_RoleName)
| where '*' in (selected_roles) or array_length(selected_roles) == 0 or cloud_RoleName in (selected_roles)
| extend gen_ai_system = tostring(customDimensions['gen_ai.system'])
| extend gen_ai_operation = tostring(customDimensions['gen_ai.operation.name'])
| extend request_model = tostring(customDimensions['gen_ai.request.model'])
| extend response_model = tostring(customDimensions['gen_ai.response.model'])
| extend model = iff(isnotempty(request_model), request_model, iff(isnotempty(response_model), response_model, '(unknown)'))
| extend input_tokens = coalesce(tolong(customDimensions['gen_ai.usage.input_tokens']), 0)
| extend output_tokens = coalesce(tolong(customDimensions['gen_ai.usage.output_tokens']), 0)
| extend total_tokens = input_tokens + output_tokens
| extend duration_ms = todouble(duration / 1ms)
| summarize
    total_spans = count(),
    failed_spans = countif(success == false),
    failure_rate_pct = round(100.0 * todouble(countif(success == false)) / count(), 2)
  by cloud_RoleName
| order by failure_rate_pct desc
| render barchart with (xcolumn=cloud_RoleName, ycolumns=failure_rate_pct, title='Failure rate by role (%)')
```

## KQL caveats

- `customDimensions['gen_ai.usage.input_tokens']` and `customDimensions['gen_ai.usage.output_tokens']` arrive as strings, so the workbook casts them with `tolong()`.
- The workbook uses `union dependencies, requests` because most model/tool spans land in `dependencies`, while the orchestrator’s top-level `chat ...` spans land in `requests`.
- The workbook filters to rows where `customDimensions` contains `gen_ai.system`; if a role emits only non-GenAI spans, it won’t appear in these tiles.
- Xi'an currently has no separate `cloud_RoleName`; its activity rolls into the orchestrator span tree.
- Copilot SDK / collector spans may appear under `copilot` or `github-copilot` depending on collector resource attributes.

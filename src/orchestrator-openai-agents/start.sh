#!/bin/sh
set -e

# Start the OTel Collector sidecar (OTLP receiver -> Azure Monitor exporter).
# Only used when an OTLP endpoint path is desired; tracing.py exports to Azure
# Monitor directly when APPLICATIONINSIGHTS_CONNECTION_STRING is set, so launch
# the sidecar only if an explicit OTLP endpoint is configured to avoid
# duplicate export.
if [ -x /usr/local/bin/otelcol-contrib ] \
   && [ -n "${APPLICATIONINSIGHTS_CONNECTION_STRING}" ] \
   && [ -n "${OTEL_EXPORTER_OTLP_ENDPOINT}" ]; then
  echo "[start.sh] launching otelcol-contrib sidecar on 127.0.0.1:4318"
  /usr/local/bin/otelcol-contrib --config /app/user_agent/otel-collector-config.yaml &
  COLLECTOR_PID=$!
  sleep 1
  echo "[start.sh] otelcol-contrib started (pid=${COLLECTOR_PID})"
else
  echo "[start.sh] otelcol-contrib not started (binary missing or sidecar export not requested)"
fi

exec python main.py

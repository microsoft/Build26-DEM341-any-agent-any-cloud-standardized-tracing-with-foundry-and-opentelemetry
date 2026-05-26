#!/bin/sh
set -e

# Start the OTel Collector sidecar (OTLP receiver -> Azure Monitor exporter).
# Used to bridge Copilot SDK's OTLP-only telemetry into Application Insights.
if [ -x /usr/local/bin/otelcol-contrib ] && [ -n "${APPLICATIONINSIGHTS_CONNECTION_STRING}" ]; then
  echo "[start.sh] launching otelcol-contrib sidecar on 127.0.0.1:4318"
  /usr/local/bin/otelcol-contrib --config /app/user_agent/otel-collector-config.yaml &
  COLLECTOR_PID=$!
  # Give the collector a moment to bind its listener before main.py starts emitting.
  sleep 1
  echo "[start.sh] otelcol-contrib started (pid=${COLLECTOR_PID})"
else
  echo "[start.sh] otelcol-contrib not started (binary missing or APPLICATIONINSIGHTS_CONNECTION_STRING unset)"
fi

exec python main.py

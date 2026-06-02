#!/bin/sh
set -e

# Start the OTel Collector sidecar (OTLP receiver -> Azure Monitor exporter)
# only when an explicit OTLP endpoint is configured. The Foundry responses-host
# runtime already exports directly to Azure Monitor via
# APPLICATIONINSIGHTS_CONNECTION_STRING, so we avoid launching an idle/duplicate
# sidecar unless an OTLP path is explicitly requested.
if [ -x /usr/local/bin/otelcol-contrib ] \
   && [ -n "${APPLICATIONINSIGHTS_CONNECTION_STRING}" ] \
   && [ -n "${OTEL_EXPORTER_OTLP_ENDPOINT}" ]; then
  echo "[start.sh] launching otelcol-contrib sidecar on 127.0.0.1:4318"
  /usr/local/bin/otelcol-contrib --config /app/user_agent/otel-collector-config.yaml &
  COLLECTOR_PID=$!
  # Give the collector a moment to bind its listener before main.py starts emitting.
  sleep 1
  echo "[start.sh] otelcol-contrib started (pid=${COLLECTOR_PID})"
else
  echo "[start.sh] otelcol-contrib not started (binary missing or sidecar export not requested)"
fi

exec python main.py

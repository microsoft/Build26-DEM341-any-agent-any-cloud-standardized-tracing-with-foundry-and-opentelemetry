FROM python:3.12-slim

# OpenTelemetry Collector Contrib version (includes the azuremonitor exporter).
ARG OTELCOL_VERSION=0.152.0
ARG TARGETARCH=amd64

# Install otelcol-contrib sidecar so the Copilot SDK CLI can emit OTLP traces
# that get forwarded to Application Insights via the azuremonitor exporter.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /tmp/otelcol.tar.gz \
        "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v${OTELCOL_VERSION}/otelcol-contrib_${OTELCOL_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && tar -xzf /tmp/otelcol.tar.gz -C /usr/local/bin otelcol-contrib \
    && rm /tmp/otelcol.tar.gz \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . user_agent/
WORKDIR /app/user_agent

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8088

CMD ["./start.sh"]

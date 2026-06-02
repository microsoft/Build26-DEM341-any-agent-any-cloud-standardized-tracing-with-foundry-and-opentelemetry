FROM python:3.12-slim

# OpenTelemetry Collector Contrib version (includes the azuremonitor exporter).
ARG OTELCOL_VERSION=0.152.0
ARG TARGETARCH=amd64

# Install otelcol-contrib sidecar so OTLP traces can be forwarded to Application
# Insights via the azuremonitor exporter when running behind an OTLP endpoint.
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

# Upgrade pip first: the slim image ships a pip whose importlib metadata
# backend mis-parses valid extras markers (e.g. `extra == "hosting"`),
# raising InvalidVersion. Newer pip fixes this resolver bug.
RUN python -m pip install --no-cache-dir --upgrade "pip>=24.2"

RUN pip install --no-cache-dir -r requirements.txt

# Surface any linux/amd64 dependency resolution conflicts in the build log
# (informational; does not fail the build on benign version warnings).
RUN pip check || true

EXPOSE 8088

CMD ["./start.sh"]

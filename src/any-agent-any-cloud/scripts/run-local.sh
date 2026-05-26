#!/usr/bin/env bash
# Start all four local services for the demo. Writes PIDs and logs to logs/.
# Usage: ./scripts/run-local.sh start | stop | status | tail
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

AI_CONN="$(cat "$ROOT/infra/appinsights-conn.txt")"
export APPLICATIONINSIGHTS_CONNECTION_STRING="$AI_CONN"
export DEMO_SHARED_SECRET="devsecret"

if [[ -f "$ROOT/infra/aoai.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/infra/aoai.env"; set +a
fi

if [[ -f "$ROOT/infra/gcp.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/infra/gcp.env"; set +a
fi
if [[ -f "$ROOT/infra/bangalore-gcp.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/infra/bangalore-gcp.env"; set +a
fi

if [[ -f "$ROOT/infra/cloud-endpoints.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/infra/cloud-endpoints.env"; set +a
fi

if [[ -f "$ROOT/infra/foundry-endpoints.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$ROOT/infra/foundry-endpoints.env"; set +a
fi

start_one() {
  local name="$1" dir="$2" cmd="$3" port="$4"
  if [[ -f "$LOG_DIR/$name.pid" ]] && kill -0 "$(cat "$LOG_DIR/$name.pid")" 2>/dev/null; then
    echo "[$name] already running (pid $(cat "$LOG_DIR/$name.pid"))"
    return
  fi
  echo "[$name] starting on :$port  (log: $LOG_DIR/$name.log)"
  ( cd "$dir" && eval "$cmd" >"$LOG_DIR/$name.log" 2>&1 & echo $! > "$LOG_DIR/$name.pid" )
}

stop_one() {
  local name="$1"
  if [[ -f "$LOG_DIR/$name.pid" ]]; then
    local pid; pid="$(cat "$LOG_DIR/$name.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "[$name] stopping pid $pid (and children)"
      # kill the whole process group; fall back to children of pid
      local kids; kids="$(pgrep -P "$pid" 2>/dev/null | tr '\n' ' ' || true)"
      kill -TERM "$pid" $kids 2>/dev/null || true
      sleep 1
      kill -KILL "$pid" $kids 2>/dev/null || true
    fi
    rm -f "$LOG_DIR/$name.pid"
  fi
}

cmd_start() {
  start_one seattle "$ROOT/agents/seattle-langgraph" \
    "AWS_REGION=\${AWS_REGION:-us-west-2} .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080" 8080
  start_one bangalore "$ROOT/agents/bangalore-adk" \
    "GOOGLE_CLOUD_PROJECT=\${GOOGLE_CLOUD_PROJECT:-langgraph-agent-488906} GOOGLE_CLOUD_REGION=\${GOOGLE_CLOUD_REGION:-us-central1} GOOGLE_CLOUD_LOCATION=\${GOOGLE_CLOUD_LOCATION:-us-central1} GOOGLE_GENAI_USE_VERTEXAI=true VERTEX_MODEL_ID=\${VERTEX_MODEL_ID:-gemini-2.5-flash-lite} .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8081" 8081
  start_one orchestrator "$ROOT/orchestrator" \
    "BANGALORE_AGENT_URL=http://localhost:8081 .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000" 8000
}

cmd_stop() {
  for s in orchestrator bangalore seattle; do stop_one "$s"; done
}

cmd_status() {
  for s in seattle bangalore orchestrator; do
    if [[ -f "$LOG_DIR/$s.pid" ]] && kill -0 "$(cat "$LOG_DIR/$s.pid")" 2>/dev/null; then
      echo "[$s] up (pid $(cat "$LOG_DIR/$s.pid"))"
    else
      echo "[$s] down"
    fi
  done
}

cmd_tail() {
  tail -n 40 -F "$LOG_DIR"/*.log
}

case "${1:-start}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  tail) cmd_tail ;;
  restart) cmd_stop; sleep 1; cmd_start ;;
  *) echo "usage: $0 {start|stop|status|tail|restart}"; exit 1 ;;
esac

#!/usr/bin/env python3
"""Run a lightweight offline evaluation sweep against the deployed Foundry orchestrator.

This helper intentionally does not invoke any evaluation APIs. It simply replays a JSONL
prompt set against the orchestrator's Responses endpoint, extracts the returned trace ID and
best-effort routed city from the text response, and writes a CSV for later analysis.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

DEFAULT_DATASET = Path(__file__).with_name("eval-dataset.jsonl")
DEFAULT_OUTPUT = Path(__file__).with_name("offline-eval-results.csv")
TRACE_ID_RE = re.compile(r"trace_id:\s*([0-9a-f]{32})", re.IGNORECASE)
HEADING_RE = re.compile(r"^###\s+(.+?)\s*\(agent:", re.IGNORECASE | re.MULTILINE)


@dataclass
class DatasetRow:
    query: str
    expected_city: str
    intent: str
    notes: str


@dataclass
class ResultRow:
    query: str
    expected_city: str
    intent: str
    notes: str
    routed_city: str
    route_match: bool
    trace_id: str
    latency_ms: int
    response_id: str
    status: str
    error: str
    response_text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="Path to the JSONL dataset")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to the output CSV")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds (informational only)")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for smoke runs")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def load_dataset(path: Path, limit: int = 0) -> list[DatasetRow]:
    rows: list[DatasetRow] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            missing = [key for key in ("query", "expected_city", "intent", "notes") if key not in payload]
            if missing:
                raise ValueError(f"{path}:{line_no} missing keys: {', '.join(missing)}")
            rows.append(
                DatasetRow(
                    query=str(payload["query"]),
                    expected_city=str(payload["expected_city"]),
                    intent=str(payload["intent"]),
                    notes=str(payload["notes"]),
                )
            )
            if limit and len(rows) >= limit:
                break
    if not rows:
        raise ValueError(f"Dataset is empty: {path}")
    return rows


def extract_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    text_parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", []) or []:
            text_value = getattr(content, "text", "") or ""
            if text_value:
                text_parts.append(str(text_value))
    return "\n".join(part.strip() for part in text_parts if part.strip()).strip()


def extract_trace_id(text: str) -> str:
    match = TRACE_ID_RE.search(text)
    return match.group(1).lower() if match else ""


def normalize_city(label: str) -> str:
    lowered = label.lower()
    if "seattle" in lowered:
        return "seattle"
    if "bangalore" in lowered or "bengaluru" in lowered:
        return "bangalore"
    if "xi'an" in lowered or "xian" in lowered or "xi an" in lowered:
        return "xian"
    if "copilot" in lowered:
        return "copilot"
    return "unknown"


def extract_routed_city(text: str) -> str:
    heading = HEADING_RE.search(text)
    if heading:
        return normalize_city(heading.group(1))
    return normalize_city(text)


def create_client(endpoint: str) -> tuple[AIProjectClient, Any]:
    project_client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return project_client, project_client.get_openai_client()


def invoke_agent(openai_client: Any, agent_name: str, query: str, agent_version: str | None = None) -> Any:
    agent_reference: dict[str, Any] = {"type": "agent_reference", "name": agent_name}
    if agent_version:
        agent_reference["version"] = agent_version
    return openai_client.responses.create(input=query, extra_body={"agent_reference": agent_reference})


def write_results(path: Path, rows: list[ResultRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(ResultRow.__annotations__.keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_summary(rows: list[ResultRow]) -> None:
    total = len(rows)
    succeeded = sum(1 for row in rows if not row.error)
    matched = sum(1 for row in rows if row.route_match)
    latencies = [row.latency_ms for row in rows if row.latency_ms > 0]

    print("\nSummary")
    print("=======")
    print(f"Rows:           {total}")
    print(f"Succeeded:      {succeeded}")
    print(f"Route matches:  {matched}/{total} ({(matched / total) * 100:.1f}%)")
    if latencies:
        print(f"Latency avg ms: {statistics.mean(latencies):.0f}")
        print(f"Latency p50 ms: {statistics.median(latencies):.0f}")
        print(f"Latency max ms: {max(latencies)}")

    print("\nBy expected city")
    print("----------------")
    print(f"{'expected':<10} {'count':>5} {'matched':>7}")
    for city in sorted({row.expected_city for row in rows}):
        city_rows = [row for row in rows if row.expected_city == city]
        city_matched = sum(1 for row in city_rows if row.route_match)
        print(f"{city:<10} {len(city_rows):>5} {city_matched:>7}")


def main() -> int:
    args = parse_args()
    endpoint = require_env("FOUNDRY_PROJECT_ENDPOINT")
    agent_name = os.getenv("FOUNDRY_ORCHESTRATOR_AGENT", "foundry-orchestrator").strip() or "foundry-orchestrator"
    agent_version = os.getenv("FOUNDRY_ORCHESTRATOR_VERSION", "").strip() or None

    dataset = load_dataset(args.dataset, limit=args.limit)
    results: list[ResultRow] = []

    project_client, openai_client = create_client(endpoint)
    try:
        for row in dataset:
            started = time.perf_counter()
            response_text = ""
            trace_id = ""
            routed_city = "unknown"
            response_id = ""
            status = "failed"
            error = ""
            try:
                response = invoke_agent(openai_client, agent_name=agent_name, query=row.query, agent_version=agent_version)
                response_id = str(getattr(response, "id", "") or "")
                status = str(getattr(response, "status", "completed") or "completed")
                response_text = extract_text(response)
                trace_id = extract_trace_id(response_text)
                routed_city = extract_routed_city(response_text)
            except Exception as exc:  # pragma: no cover - network/runtime failure path
                error = f"{type(exc).__name__}: {exc}"
                status = "error"
            latency_ms = int((time.perf_counter() - started) * 1000)
            results.append(
                ResultRow(
                    query=row.query,
                    expected_city=row.expected_city,
                    intent=row.intent,
                    notes=row.notes,
                    routed_city=routed_city,
                    route_match=routed_city == row.expected_city,
                    trace_id=trace_id,
                    latency_ms=latency_ms,
                    response_id=response_id,
                    status=status,
                    error=error,
                    response_text=response_text,
                )
            )
    finally:
        close = getattr(project_client, "close", None)
        if callable(close):
            close()

    write_results(args.output, results)
    print_summary(results)
    print(f"\nWrote: {args.output}")
    print(f"Timeout setting supplied: {args.timeout}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

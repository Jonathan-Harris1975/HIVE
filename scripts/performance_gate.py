#!/usr/bin/env python3
"""Deterministic, dependency-isolated HIVE API performance regression gate."""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Keep the benchmark self-contained and free from live storage/provider calls.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("ADMIN_BEARER_TOKEN", "ci-performance-token")
os.environ.setdefault("DATABASE_ENABLED", "false")
os.environ.setdefault("D1_ENABLED", "false")
os.environ.setdefault("VECTORIZE_ENABLED", "false")
os.environ.setdefault("EMBEDDINGS_ENABLED", "false")
os.environ.setdefault("AI_SEARCH_ENABLED", "false")
os.environ.setdefault("ALLOWED_HOSTS", "testserver,127.0.0.1,localhost")
os.environ.setdefault("OPS_EVENT_INGEST_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * p))
    return ordered[index]


def measure(client: TestClient, path: str, requests: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    samples: list[float] = []
    errors = 0
    statuses: dict[int, int] = {}
    for _ in range(requests):
        started = time.perf_counter()
        response = client.get(path, headers=headers)
        samples.append((time.perf_counter() - started) * 1000)
        statuses[response.status_code] = statuses.get(response.status_code, 0) + 1
        if response.status_code >= 400:
            errors += 1
    return {
        "path": path,
        "requests": requests,
        "errors": errors,
        "statuses": statuses,
        "meanMs": round(statistics.fmean(samples), 3),
        "p50Ms": round(percentile(samples, 0.50), 3),
        "p95Ms": round(percentile(samples, 0.95), 3),
        "maxMs": round(max(samples), 3),
    }


def limit(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def main() -> None:
    output = Path(os.environ.get("HIVE_PERF_REPORT_PATH", "hive-performance.json"))
    admin_headers = {"Authorization": "Bearer ci-performance-token"}
    with TestClient(app) as client:
        livez = measure(client, "/livez", 120)
        runtime = measure(client, "/v1/system/runtime-stats", 60, headers=admin_headers)

    result = {
        "service": "HIVE",
        "python": sys.version.split()[0],
        "externalCalls": 0,
        "livez": livez,
        "runtimeStats": runtime,
        "thresholds": {
            "livezMeanMs": limit("HIVE_PERF_LIVEZ_MEAN_MAX_MS", 15),
            "livezP95Ms": limit("HIVE_PERF_LIVEZ_P95_MAX_MS", 30),
            "runtimeMeanMs": limit("HIVE_PERF_RUNTIME_MEAN_MAX_MS", 30),
            "runtimeP95Ms": limit("HIVE_PERF_RUNTIME_P95_MAX_MS", 60),
            "errors": 0,
        },
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))

    limits = result["thresholds"]
    violations: list[str] = []
    if livez["errors"] != 0:
        violations.append(f"/livez errors={livez['errors']} must be 0")
    if runtime["errors"] != 0:
        violations.append(f"/v1/system/runtime-stats errors={runtime['errors']} must be 0")
    if livez["meanMs"] > limits["livezMeanMs"]:
        violations.append(f"/livez mean={livez['meanMs']}ms exceeds {limits['livezMeanMs']}ms")
    if livez["p95Ms"] > limits["livezP95Ms"]:
        violations.append(f"/livez p95={livez['p95Ms']}ms exceeds {limits['livezP95Ms']}ms")
    if runtime["meanMs"] > limits["runtimeMeanMs"]:
        violations.append(f"runtime-stats mean={runtime['meanMs']}ms exceeds {limits['runtimeMeanMs']}ms")
    if runtime["p95Ms"] > limits["runtimeP95Ms"]:
        violations.append(f"runtime-stats p95={runtime['p95Ms']}ms exceeds {limits['runtimeP95Ms']}ms")

    if violations:
        print("HIVE performance gate failed:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        raise SystemExit(1)
    print("HIVE performance gate passed.")


if __name__ == "__main__":
    main()

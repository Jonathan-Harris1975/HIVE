#!/usr/bin/env python3
"""Run MyPy over the full HIVE backend and reject type regressions.

HIVE currently has a finite set of legacy typing findings.  The baseline makes
that debt explicit without disabling MyPy: every backend module is still
checked, and CI fails if a new error fingerprint appears or an existing error
fingerprint becomes more frequent.  Removing legacy errors is always allowed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / ".mypy-baseline.json"
ERROR_RE = re.compile(r"^(backend/app/[^:]+):(\d+): error: (.*?)\s+\[([^\]]+)\]\s*$")


def _fingerprints(lines: Iterable[str]) -> Counter[str]:
    results: Counter[str] = Counter()
    for raw_line in lines:
        line = raw_line.strip()
        match = ERROR_RE.match(line)
        if not match:
            continue
        path, _line_number, message, code = match.groups()
        results[f"{path}|{code}|{message}"] += 1
    return results


def _load_baseline() -> tuple[str, Counter[str]]:
    payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    version = str(payload.get("mypy_version", "")).strip()
    raw = payload.get("fingerprints")
    if not version or not isinstance(raw, dict):
        raise RuntimeError("Invalid .mypy-baseline.json")
    return version, Counter({str(key): int(value) for key, value in raw.items()})


def _installed_mypy_version() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"mypy\s+([0-9]+(?:\.[0-9]+)+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse MyPy version from: {result.stdout.strip()!r}")
    return match.group(1)


def main() -> int:
    expected_version, baseline = _load_baseline()
    installed_version = _installed_mypy_version()
    if installed_version != expected_version:
        print(
            f"MyPy version mismatch: baseline={expected_version}, installed={installed_version}. "
            "Update the pin and baseline together.",
            file=sys.stderr,
        )
        return 2

    command = [
        sys.executable,
        "-m",
        "mypy",
        "backend/app",
        "--no-incremental",
        "--show-error-codes",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if combined:
        print(combined, end="" if combined.endswith("\n") else "\n")

    current = _fingerprints(combined.splitlines())
    if result.returncode == 0:
        print("MyPy regression guard: clean backend; baseline can now be removed.")
        return 0

    if not current:
        print(
            "MyPy failed but no typed error fingerprints were parsed; refusing to hide an infrastructure/tooling failure.",
            file=sys.stderr,
        )
        return result.returncode or 1

    regressions = current - baseline
    if regressions:
        print("\nMyPy regression guard found NEW/INCREASED typing errors:", file=sys.stderr)
        for fingerprint, count in sorted(regressions.items()):
            print(f"  +{count} {fingerprint}", file=sys.stderr)
        return 1

    removed = baseline - current
    print(
        f"MyPy regression guard: {sum(current.values())} known errors remain; "
        f"{sum(removed.values())} baseline errors have been removed; no new type regressions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

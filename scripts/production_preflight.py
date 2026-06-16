#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.production import build_readiness_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate HIVE production configuration without printing secrets.")
    parser.add_argument(
        "--allow-warnings",
        action="store_true",
        help="Exit successfully when only warnings are present (errors always fail).",
    )
    args = parser.parse_args()

    report = build_readiness_report(Settings())
    print(json.dumps(report.detailed_payload(), indent=2, sort_keys=True))

    if not report.ready:
        return 1
    if report.warnings and not args.allow_warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

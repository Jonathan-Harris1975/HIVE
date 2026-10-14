#!/usr/bin/env python3
"""Render a validated Council result as a readable, exact-run Markdown report."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from r2_result_channel import validate_result


def _markdown_item(value: Any) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Council evidence and blocker entries must be strings")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 1000:
        raise RuntimeError("Council evidence and blocker entries must be non-empty and at most 1000 characters")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|])", r"\\\1", normalized)


def render_report(payload: dict[str, Any]) -> str:
    evidence = payload.get("evidence")
    blockers = payload.get("blockers")
    if not isinstance(evidence, list) or not isinstance(blockers, list):
        raise RuntimeError("Council evidence and blockers must be lists")

    disposition = payload["disposition"]
    lines = [
        "# Repository Council report",
        "",
        f"- Repository: {payload['repository']}",
        f"- Evaluated default-branch SHA: {payload['sha']}",
        f"- Council run: {payload['run_id']} (attempt {payload['run_attempt']})",
        f"- Disposition: {disposition}",
        "- Certification complete: yes",
        "",
        "## Evidence",
        "",
    ]
    lines.extend(f"- {_markdown_item(item)}" for item in evidence)
    if not evidence:
        lines.append("- No evidence entries were returned.")
    lines.extend(["", "## Blockers", ""])
    lines.extend(f"- {_markdown_item(item)}" for item in blockers)
    if not blockers:
        lines.append("- None reported.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    args = parser.parse_args()

    validate_result(args.source, args.repository, args.sha, args.run_id, args.run_attempt)
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(payload), encoding="utf-8")
    print("Validated Council report rendered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Conservative CI runtime change classification."""
from __future__ import annotations

import sys

DOC_ROOT = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md"}


def needs_runtime_gates(paths: list[str]) -> bool:
    """Unknown or empty diffs fail closed; only known documentation is exempt."""
    if not paths:
        return True
    return any(not (path.startswith("docs/") and path != "docs/" or path in DOC_ROOT) for path in paths)


if __name__ == "__main__":
    changed = [line for line in sys.stdin.read().splitlines() if line]
    print("true" if needs_runtime_gates(changed) else "false")

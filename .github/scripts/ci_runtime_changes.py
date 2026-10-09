#!/usr/bin/env python3
"""Fail-closed CI runtime change classification."""
from __future__ import annotations

import sys

DOC_ROOT = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md"}
DOC_SUFFIXES = (".md", ".rst", ".txt")


def needs_runtime_gates(paths: list[str]) -> bool:
    """Skip runtime gates only for a non-empty set of known documentation files."""
    if not paths:
        return True
    for path in paths:
        if path in DOC_ROOT:
            continue
        # Documentation directories can contain executable scripts or configs.
        if path.startswith("docs/") and path.endswith(DOC_SUFFIXES):
            continue
        return True
    return False


if __name__ == "__main__":
    changed = [line for line in sys.stdin.read().splitlines() if line]
    print("true" if needs_runtime_gates(changed) else "false")

"""Regression test: dry run must never dispatch or mutate GitHub state."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/weekend_orchestrator.py"
spec = importlib.util.spec_from_file_location("weekend_orchestrator", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ReadOnlyApi:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        if path.endswith("/commits/main"):
            return {"sha": "a" * 40}
        if "/issues?state=open" in path:
            return []
        raise AssertionError(f"Unexpected GET: {path}")

    def pages(self, path, key=None):
        self.calls.append(("GET", path))
        return []

    def call(self, method, path, payload=None, expected=(200, 201, 202, 204)):
        raise AssertionError(f"Dry run attempted mutation: {method} {path}")


def test_dry_run_is_read_only():
    api = ReadOnlyApi()
    cfg = module.Config(repo="example/repo", token="test", dry_run=True)
    orchestrator = module.Orchestrator(cfg, api=api)
    assert orchestrator.run() == 0
    assert all(method == "GET" for method, _ in api.calls)
    assert [item["outcome"] for item in orchestrator.stages] == ["read-only", "not-verified"]

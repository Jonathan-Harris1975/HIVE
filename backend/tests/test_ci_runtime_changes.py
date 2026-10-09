"""Regression cases for CI runtime change classification."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/ci_runtime_changes.py"
spec = importlib.util.spec_from_file_location("ci_runtime_changes", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        ([], True),
        (["README.md"], False),
        (["docs/operations.md"], False),
        (["README.md", "backend/app/main.py"], True),
        (["backend/app/main.py"], True),
        (["requirements.txt"], True),
        (["requirements-dev.txt"], True),
        ([".github/workflows/security.yml"], True),
        ([".github/scripts/weekend_orchestrator.py"], True),
        (["Dockerfile"], True),
        (["nixpacks.toml"], True),
        (["runtime.txt"], True),
        (["docs/operations.md", "config/production.yml"], True),
        (["unknown/new-file.md"], True),
    ],
)
def test_runtime_change_detection(paths, expected):
    assert module.needs_runtime_gates(paths) is expected

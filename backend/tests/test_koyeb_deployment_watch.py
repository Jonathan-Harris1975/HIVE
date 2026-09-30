from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_watcher() -> ModuleType:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "hive_watch_koyeb_deployment", ROOT / "scripts" / "watch_koyeb_deployment.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


@pytest.fixture()
def watcher() -> ModuleType:
    return _load_watcher()


def test_bare_service_name_is_resolved_to_the_koyeb_service_id(watcher: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: Any) -> _Result:
        calls.append(args)
        return _Result(0, json.dumps({"services": [{"id": "22222222-2222-2222-2222-222222222222", "name": "hive"}]}))

    monkeypatch.setattr(watcher.subprocess, "run", fake_run)

    assert watcher._resolve_service("hive", "token") == "22222222-2222-2222-2222-222222222222"
    assert calls and calls[0][1:3] == ["services", "list"]


def test_cli_identifiers_pass_through_without_an_extra_lookup(watcher: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> _Result:
        raise AssertionError("the Koyeb CLI must not be called for a resolved identifier")

    monkeypatch.setattr(watcher.subprocess, "run", fail)

    for value in ("5102061c-a0d2-4195-84d3-0f75b8b8eaa2", "liable-loreen/hive"):
        assert watcher._resolve_service(value, "token") == value


def test_ambiguous_or_failed_lookup_keeps_the_original_reference(watcher: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    ambiguous = json.dumps({"services": [{"id": "a", "name": "hive"}, {"id": "b", "name": "hive"}]})
    monkeypatch.setattr(watcher.subprocess, "run", lambda *_args, **_kwargs: _Result(0, ambiguous))
    assert watcher._resolve_service("hive", "token") == "hive"

    monkeypatch.setattr(watcher.subprocess, "run", lambda *_args, **_kwargs: _Result(1, ""))
    assert watcher._resolve_service("hive", "token") == "hive"

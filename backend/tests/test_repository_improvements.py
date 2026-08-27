from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import repository_improvements


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env="test",
        d1_enabled=False,
        repository_temp_dir=str(tmp_path / "runtime"),
        openrouter_api_key="test-key",
        production_require_r2=False,
    )


def _record(root: Path):
    manifest = SimpleNamespace(
        repository_id="HIVE",
        source_filename="HIVE-main.zip",
        fingerprint="fingerprint-1",
        indexed_version=3,
        file_count=2,
        total_bytes=100,
        languages={"Python": 2},
        dependencies=[SimpleNamespace(manifest_path="pyproject.toml", ecosystem="python", declared=["fastapi"])],
    )
    return SimpleNamespace(repository_id="HIVE", workdir=root, manifest=manifest)


def test_improvement_paths_are_confined_to_repository() -> None:
    assert repository_improvements._normalise_relative_path("backend/app.py") == "backend/app.py"
    with pytest.raises(repository_improvements.RepositoryImprovementError):
        repository_improvements._normalise_relative_path("../outside.py")
    with pytest.raises(repository_improvements.RepositoryImprovementError):
        repository_improvements._normalise_relative_path("/etc/passwd")
    with pytest.raises(repository_improvements.RepositoryImprovementError):
        repository_improvements._normalise_relative_path(".env")


def test_model_context_redacts_secret_values() -> None:
    source = (
        'api_key = "abcdefghijklmnop1234"\n'
        'Authorization = "Bearer abcdefghijklmnopqrstuvwxyz"\n'
        f'AWS = "{"AKIA" + "1234567890ABCDEF"}"\n'
    )
    redacted = repository_improvements._redact_model_context(source)

    assert "abcdefghijklmnop1234" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert ("AKIA" + "1234567890ABCDEF") not in redacted
    assert redacted.count("[HIVE REDACTED]") == 3


def test_coding_request_contains_repository_specific_evidence(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "backend").mkdir()
    (root / "backend" / "app.py").write_text("print('old')\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='hive'\n", encoding="utf-8")
    record = _record(root)
    intelligence = {
        "repository_id": "HIVE",
        "repository_context": {
            "repository_id": "HIVE",
            "fingerprint": "fingerprint-1",
            "languages": {"Python": 2},
            "dependency_manifests": [{"path": "pyproject.toml", "ecosystem": "python"}],
        },
        "summary": {"headline": "HIVE requires one repair"},
        "findings": [
            {
                "title": "Fix import validation",
                "details": {"unresolved": [{"path": "backend/app.py", "module": "missing"}]},
            }
        ],
    }
    files = repository_improvements._read_context_files(record, intelligence)
    system, user = repository_improvements._model_request("HIVE", intelligence, files)

    assert "production-grade code changes" in system
    assert "fingerprint-1" in user
    assert "backend/app.py" in user
    assert "print('old')" in user


@pytest.mark.asyncio
async def test_improvement_job_edits_isolated_copy_and_packages_downloads(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# HIVE\n", encoding="utf-8")
    record = _record(root)
    settings = _settings(tmp_path)
    intelligence = {
        "repository_id": "HIVE",
        "repository_context": {"repository_id": "HIVE", "fingerprint": "fingerprint-1"},
        "summary": {"headline": "HIVE needs a small repair", "finding_count": 1, "qa_score": 0.9},
        "findings": [{"title": "Improve app", "details": {"path": "app.py"}}],
    }

    monkeypatch.setattr(repository_improvements, "get_repository", lambda _repository_id: record)
    monkeypatch.setattr(repository_improvements, "is_rehydrated", lambda _record: False)
    monkeypatch.setattr(repository_improvements, "_extract_latest_intelligence", lambda *_args: intelligence)

    async def fake_model(*_args, **_kwargs):
        return (
            {
                "summary": "Updated the application constant safely.",
                "changes": [
                    {
                        "path": "app.py",
                        "action": "replace",
                        "content": "VALUE = 2\n",
                        "rationale": "Addresses the supplied repository finding.",
                    }
                ],
                "remaining_risks": ["Run native CI before deployment."],
            },
            "coding/test-model",
        )

    monkeypatch.setattr(repository_improvements, "_run_model", fake_model)
    monkeypatch.setattr(
        repository_improvements,
        "run_repository_qa_for_workdir",
        lambda *_args, **_kwargs: SimpleNamespace(
            public_payload=lambda: {
                "repository_id": "HIVE",
                "score": 1.0,
                "warning_count": 0,
                "checks": [
                    {"name": "build_verification", "status": "ok", "details": {}, "summary": "ok"},
                    {"name": "security_scanning", "status": "ok", "details": {}, "summary": "ok"},
                ],
            }
        ),
    )
    monkeypatch.setattr(repository_improvements, "_store_artifact", lambda *_args, name, **_kwargs: (f"key/{name}", True))
    monkeypatch.setattr(repository_improvements, "D1MetadataStore", lambda _settings: SimpleNamespace(enabled=False))

    job_id = "job-test"
    repository_improvements._JOBS.clear()
    await repository_improvements._run_job(settings, job_id, "HIVE")
    job = repository_improvements._JOBS[job_id]

    assert job["status"] == "completed"
    assert job["change_count"] == 1
    assert any("native CI" in risk for risk in job["remaining_risks"])
    assert root.joinpath("app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    changed_zip = Path(job["local_artifacts"]["changed_files"])
    full_zip = Path(job["local_artifacts"]["updated_repository"])
    assert changed_zip.is_file()
    assert full_zip.is_file()

    with zipfile.ZipFile(changed_zip) as archive:
        assert archive.read("app.py").decode() == "VALUE = 2\n"
        assert "HIVE-IMPROVEMENT-REPORT.json" in archive.namelist()
    with zipfile.ZipFile(full_zip) as archive:
        assert archive.read("app.py").decode() == "VALUE = 2\n"


def test_current_intelligence_rejects_cross_repository_report(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    record = _record(root)
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        repository_improvements,
        "_extract_latest_intelligence",
        lambda *_args: {
            "repository_id": "AIMS",
            "repository_context": {"repository_id": "AIMS", "fingerprint": "fingerprint-1"},
            "summary": {"finding_count": 1},
            "findings": [{"title": "Wrong repository"}],
        },
    )

    with pytest.raises(repository_improvements.RepositoryImprovementError, match="different repository"):
        repository_improvements._current_intelligence(settings, record)


def test_improvement_routes_are_registered() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/v1/repositories/{repository_id}/improvements/run" in paths
    assert "/v1/repositories/{repository_id}/improvements/latest" in paths
    assert "/v1/repositories/{repository_id}/improvements/jobs/{job_id}" in paths
    assert "/v1/repositories/{repository_id}/improvements/jobs/{job_id}/download/{kind}" in paths

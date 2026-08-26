from __future__ import annotations

from types import SimpleNamespace

from app.core.config import Settings
from app.services import repository_intelligence


def _settings() -> Settings:
    return Settings(_env_file=None, d1_enabled=False)  # type: ignore[call-arg]


def test_combined_repository_intelligence_runs_qa_once_and_preserves_evidence(monkeypatch) -> None:
    qa_payload = {
        "repository_id": "HIVE",
        "score": 0.72,
        "warning_count": 1,
        "checks": [
            {
                "name": "security_scanning",
                "status": "warning",
                "summary": "Potential secret-pattern match",
                "details": {"matches": [{"path": "backend/app.py", "line": 12}]},
            },
            {
                "name": "architecture_validation",
                "status": "ok",
                "summary": "Architecture checks passed",
                "details": {},
            },
        ],
    }
    council_payload = {
        "repository_id": "HIVE",
        "occurred_at": "2026-08-26T20:00:00+00:00",
        "overall_score": 0.58,
        "dimensions": [
            {
                "dimension": "security",
                "score": 0.0,
                "rationale": "Derived from the same QA security check",
                "confidence": "measured",
            },
            {
                "dimension": "documentation",
                "score": 0.9,
                "rationale": "Documentation present",
                "confidence": "measured",
            },
        ],
        "recommendations": ["Improve security: Derived from the same QA security check"],
        "heuristic_dimensions": [],
        "has_unmeasured_signal": False,
    }

    qa_calls = 0
    council_qa_payloads: list[dict[str, object]] = []
    history_fields: list[str] = []

    def fake_qa(_repository_id: str):
        nonlocal qa_calls
        qa_calls += 1
        return SimpleNamespace(public_payload=lambda: qa_payload)

    def fake_council(_settings, _repository_id: str, *, qa_payload=None, **_kwargs):
        council_qa_payloads.append(qa_payload)
        return SimpleNamespace(public_payload=lambda: council_payload)

    monkeypatch.setattr(repository_intelligence, "run_repository_qa", fake_qa)
    monkeypatch.setattr(repository_intelligence, "run_and_record_council", fake_council)
    monkeypatch.setattr(repository_intelligence, "D1MetadataStore", lambda _settings: object())
    monkeypatch.setattr(
        repository_intelligence,
        "append_history_entry",
        lambda _store, **kwargs: history_fields.append(kwargs["field_name"]) or {"ok": True},
    )
    monkeypatch.setattr(
        repository_intelligence,
        "update_project_dna",
        lambda *_args, **_kwargs: {"latest_qa_score": 0.72, "latest_council_score": 0.58},
    )
    monkeypatch.setattr(
        repository_intelligence,
        "_repository_context",
        lambda *_args, **_kwargs: {
            "source_filename": "HIVE-main.zip",
            "fingerprint": "abc123",
            "languages": {"Python": 42},
            "dependency_manifests": [{"path": "pyproject.toml", "ecosystem": "python", "declared_count": 12}],
            "top_level_entries": ["backend", "pyproject.toml"],
            "implicated_files": ["backend/app.py"],
        },
    )

    report = repository_intelligence.run_repository_intelligence(_settings(), "HIVE")

    assert qa_calls == 1
    assert council_qa_payloads == [qa_payload]
    assert report["qa"] == qa_payload
    assert report["council"] == council_payload
    assert report["summary"]["finding_count"] == 2
    assert report["summary"]["blocking_finding_count"] == 2
    qa_finding = next(item for item in report["findings"] if item["id"] == "qa:security_scanning")
    assert qa_finding["details"]["matches"][0]["path"] == "backend/app.py"
    assert "Address every finding" in report["improvement_prompt"]
    assert "HIVE-main.zip" in report["improvement_prompt"]
    assert "backend/app.py" in report["improvement_prompt"]
    assert report["repository_context"]["fingerprint"] == "abc123"
    assert "qa_history" in history_fields
    assert "known_issues" in history_fields
    assert "repository_intelligence_history" in history_fields

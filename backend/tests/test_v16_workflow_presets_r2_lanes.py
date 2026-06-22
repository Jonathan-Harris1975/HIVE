from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.workflow_presets import get_workflow_preset


def _reset_settings(monkeypatch, tmp_path, **env):
    from app.core.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hive.sqlite3'}")
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def test_v16_health_reports_workflow_and_r2_lane_flags(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        R2_BUCKET_AUDITS="audits",
        R2_PUBLIC_BASE_URL_AUDITS="https://audits.example.test",
        R2_BUCKET_HIVE_SKILLS="hive-skills",
        R2_PUBLIC_BASE_URL_HIVE_SKILLS="https://skills.example.test",
    )
    client = TestClient(app)

    body = client.get("/health").json()

    assert body["build"] == "v1.26.12-catalogue-metadata"
    assert body["workflow_presets_enabled"] is True
    assert body["r2_ecosystem_lanes_enabled"] is True
    lanes = body["storage_flags"]["r2"]["ecosystem_lanes_configured"]
    assert "audits" in lanes
    assert "hive_skills" in lanes


def test_workflow_presets_endpoint_lists_presets(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/v1/workflow-presets")

    assert response.status_code == 200
    body = response.json()
    names = {preset["name"] for preset in body["presets"]}
    assert "audit_report_review" in names
    assert "repo_debug_bundle" in names
    assert body["free_tier"] is True


def test_r2_lanes_endpoint_uses_new_ecosystem_envs(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        R2_BUCKET_AUDITS="audits",
        R2_PUBLIC_BASE_URL_AUDITS="https://pub-audits.example.test",
        R2_BUCKET_PODCAST="podcast",
        R2_PUBLIC_BASE_URL_PODCAST="https://podcast.example.test",
    )
    client = TestClient(app)

    body = client.get("/v1/files/r2-lanes").json()

    assert body["ok"] is True
    lanes = {item["lane"]: item for item in body["lanes"]}
    assert lanes["audits"]["bucket"] == "audits"
    assert lanes["audits"]["public_base_url"] == "https://pub-audits.example.test"
    assert lanes["podcast"]["configured"] is True

    url = client.get(
        "/v1/files/r2-lanes/public-url",
        params={"lane": "audits", "key": "reports/latest.html"},
    ).json()
    assert url["public_url"] == "https://pub-audits.example.test/reports/latest.html"


def test_chat_with_file_applies_audit_preset_and_returns_source_chunks(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path, VECTORIZE_ENABLED="false", EMBEDDINGS_ENABLED="false")
    client = TestClient(app)
    assert client.post("/v1/db/init").json()["ok"] is True

    upload = client.post(
        "/v1/files/upload-text",
        json={
            "filename": "rams-audit.txt",
            "content": "RAMS audit finding: RSS rewrite quarantine threshold is too strict. Future QA should tune gates.",
            "test_run_id": "v16-preset",
        },
    )
    key = upload.json()["file"]["object_key"]

    response = client.post(
        "/v1/chat/with-file",
        json={
            "object_key": key,
            "message": "Summarise the highest risk finding.",
            "workflow_preset": "audit_report_review",
            "dry_run": True,
            "test_run_id": "v16-preset",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["workflow_preset"]["name"] == "audit_report_review"
    assert body["effective_mode"] == "audit"
    assert body["retrieval_metadata"]["retrieval_source"] == "sql_fallback"
    assert body["retrieval_summary"]["confidence"] in {"medium", "high"}
    assert body["source_chunks"]
    assert "quarantine threshold" in body["source_chunks"][0]["excerpt"]


def test_unknown_workflow_preset_is_rejected(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/chat/with-file",
        json={"object_key": "uploads/demo.txt", "message": "test", "workflow_preset": "unknown_lane"},
    )

    assert response.status_code == 400
    assert "audit_report_review" in response.json()["detail"]["allowed_presets"]


def test_preset_aliases_are_supported() -> None:
    assert get_workflow_preset("rams").name == "audit_report_review"
    assert get_workflow_preset("ci").name == "ci_log_analysis"

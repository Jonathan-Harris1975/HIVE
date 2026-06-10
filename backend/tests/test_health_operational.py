from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_v14_build_and_storage_flags(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["build"] == "v1.4-operational-polish"
    assert "storage_flags" in body
    assert set(body["storage_flags"]).issuperset({"r2", "sql", "d1", "vectorize", "embeddings"})


def test_healthz_is_minimal_for_mast_keepawake(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "app": "JH Ops Chat",
        "build": "v1.4-operational-polish",
    }

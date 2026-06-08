from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_upload_text_endpoint_uses_json_and_local_storage(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-text",
        json={
            "filename": "hive-r2-smoke.txt",
            "content": "HIVE R2 smoke test. Upload pipeline working.",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    file_info = body["file"]
    assert file_info["original_name"] == "hive-r2-smoke.txt"
    assert file_info["storage"] == "local"
    assert file_info["supported_for_text"] is True
    assert file_info["chunk_count"] == 1
    assert file_info["extracted_text_chars"] == len("HIVE R2 smoke test. Upload pipeline working.")
    assert file_info["object_key"].endswith("/hive-r2-smoke.txt")


def test_upload_text_sanitises_filename(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-text",
        json={"filename": "../secrets.env", "content": "not a path traversal"},
    )

    assert response.status_code == 200
    file_info = response.json()["file"]
    assert file_info["original_name"] == "secrets.env"
    assert ".." not in file_info["object_key"]

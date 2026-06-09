from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.openrouter import OpenRouterClient


def _upload_text(client: TestClient, content: str = "HIVE file read smoke test.") -> str:
    response = client.post(
        "/v1/files/upload-text",
        json={"filename": "hive-note.txt", "content": content},
    )
    assert response.status_code == 200
    return response.json()["file"]["object_key"]


def test_list_files_returns_uploaded_local_object(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client)

    response = client.get("/v1/files/list")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["storage"] == "local"
    assert body["count"] == 1
    assert body["files"][0]["key"] == key


def test_read_file_returns_uploaded_text_content(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    content = "HIVE can read back text from storage."
    key = _upload_text(client, content=content)

    response = client.get("/v1/files/read", params={"key": key})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["file"]["object_key"] == key
    assert body["content"] == content


def test_read_file_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.get("/v1/files/read", params={"key": "../secrets.env"})

    assert response.status_code == 400


def test_chat_with_file_injects_bounded_file_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client, content="This file says the R2 read layer works.")

    async def fake_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
        joined = "\n".join(message["content"] for message in payload["messages"])
        assert key in joined
        assert "This file says the R2 read layer works." in joined
        return {
            "model": payload["model"],
            "provider": "test-provider",
            "usage": {"total_tokens": 12, "cost": 0},
            "choices": [
                {
                    "message": {"content": f"Read {key}: the R2 read layer works."},
                    "finish_reason": "stop",
                }
            ],
        }

    monkeypatch.setattr(OpenRouterClient, "chat_completion", fake_chat_completion)

    response = client.post(
        "/v1/chat/with-file",
        json={
            "object_key": key,
            "message": "What does this file confirm?",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"]["object_key"] == key
    assert body["source"]["truncated"] is False
    assert "R2 read layer works" in body["reply"]


def test_file_list_runtime_error_returns_json_diagnostics(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    from app.api import files as files_api

    class BrokenStorage:
        def list_objects(self, prefix="", limit=100):  # noqa: ANN001
            raise RuntimeError("R2 list failed for prefix 'uploads/': code=AccessDenied; message=denied; http_status=403")

    monkeypatch.setattr(files_api, "_storage", lambda settings: BrokenStorage())
    client = TestClient(app)

    response = client.get("/v1/files/list")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["operation"] == "list"
    assert "bucket permissions" in body["error"]["hint"]


def test_file_diagnostics_returns_list_probe_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    from app.api import files as files_api

    class BrokenStorage:
        def list_objects(self, prefix="", limit=100):  # noqa: ANN001
            raise RuntimeError("R2 list failed for prefix 'uploads/': code=NoSuchBucket; message=missing")

    monkeypatch.setattr(files_api, "_storage", lambda settings: BrokenStorage())
    client = TestClient(app)

    response = client.get("/v1/files/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["list_probe"]["ok"] is False
    assert "bucket" in body["list_probe"]["hint"].lower()

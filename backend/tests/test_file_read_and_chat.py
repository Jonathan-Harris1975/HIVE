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
        assert key not in joined
        assert "Attached file label: hive-note.txt" in joined
        assert "This file says the R2 read layer works." in joined
        return {
            "model": payload["model"],
            "provider": "test-provider",
            "usage": {"total_tokens": 12, "cost": 0},
            "choices": [
                {
                    "message": {"content": "The R2 read layer works."},
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
    assert body["source_citation"]["label"] == "hive-note.txt"
    assert body["completion_truncated"] is False
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


def test_public_url_endpoint_returns_local_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client)

    response = client.get("/v1/files/public-url", params={"key": key})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["object_key"] == key
    assert body["storage"] == "local"
    assert body["public_url"] is None


def test_upload_base64_text_endpoint(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "base64-note.txt",
            "content_type": "text/plain",
            "content_base64": "QmFzZTY0IHVwbG9hZCB3b3Jrcy4=",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["file"]["original_name"] == "base64-note.txt"
    assert body["file"]["supported_for_text"] is True
    assert body["file"]["chunk_count"] == 1


def test_upload_base64_rejects_invalid_payload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={"filename": "bad.txt", "content_base64": "not base64 !!!"},
    )

    assert response.status_code == 400


def test_upload_base64_zip_and_inspect(monkeypatch, tmp_path):
    import base64
    import io
    import zipfile

    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("folder/a.txt", "hello")
        archive.writestr("folder/b.txt", "world")

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "sample.zip",
            "content_type": "application/zip",
            "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    key = body["file"]["object_key"]
    assert body["file"]["zip_member_count"] == 2
    assert body["file"]["supported_for_text"] is False

    inspect_response = client.get("/v1/files/zip/inspect", params={"key": key})

    assert inspect_response.status_code == 200
    inspect_body = inspect_response.json()
    assert inspect_body["ok"] is True
    assert inspect_body["zip"]["member_count"] == 2
    assert inspect_body["zip"]["file_count"] == 2
    assert inspect_body["zip"]["members_preview"][0]["filename"] == "folder/a.txt"


def test_zip_inspect_rejects_non_zip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client)

    response = client.get("/v1/files/zip/inspect", params={"key": key})

    assert response.status_code == 400

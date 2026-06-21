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


def test_chat_with_file_returns_empty_reply_diagnostic(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client, content="This file says empty reply handling works.")

    async def fake_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
        return {
            "_all_attempts_failed": True,
            "_empty_model_reply": True,
            "hive_error_code": "empty_model_reply",
            "model": payload["model"],
            "provider": None,
            "usage": None,
            "hive_attempts": [{"model": payload["model"], "empty_reply": True}],
            "choices": [
                {
                    "message": {"content": "OpenRouter returned no visible assistant text for the selected model and configured fallbacks."},
                    "finish_reason": "empty_reply",
                }
            ],
        }

    monkeypatch.setattr(OpenRouterClient, "chat_completion", fake_chat_completion)

    response = client.post(
        "/v1/chat/with-file",
        json={"object_key": key, "message": "What does this confirm?", "model": "poolside/laguna-xs.2:free", "max_tokens": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["empty_reply"] is False
    assert body["error_code"] == "empty_model_reply"
    assert "no visible assistant text" in body["reply"]



def test_chat_with_file_dry_run_skips_model_and_returns_timings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client, content="This file says dry run diagnostics work.")

    async def fail_if_called(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
        raise AssertionError("OpenRouter should not be called during dry_run")

    monkeypatch.setattr(OpenRouterClient, "chat_completion", fail_if_called)

    response = client.post(
        "/v1/chat/with-file",
        json={
            "object_key": key,
            "message": "What does this confirm?",
            "model": "poolside/laguna-xs.2:free",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["stage"] == "complete_without_model"
    assert body["source"]["object_key"] == key
    assert body["file_excerpt_chars"] > 0
    assert body["timings"]["read_file_seconds"] is not None
    assert body["timings"]["prompt_build_seconds"] is not None


def test_chat_with_file_model_timeout_returns_structured_diagnostic(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    key = _upload_text(client, content="This file says timeout diagnostics work.")

    async def slow_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
        await asyncio.sleep(2)
        return {"choices": [{"message": {"content": "too late"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(OpenRouterClient, "chat_completion", slow_chat_completion)

    response = client.post(
        "/v1/chat/with-file",
        json={
            "object_key": key,
            "message": "What does this confirm?",
            "model": "poolside/laguna-xs.2:free",
            "model_timeout_seconds": 0.5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["stage"] == "model_call"
    assert body["error_code"] == "chat_with_file_timeout"
    assert body["source"]["object_key"] == key
    assert body["timings"]["model_call_seconds"] is not None
    assert "skip_model=true" in body["hint"]


def test_file_chunk_endpoints_with_sql_store(monkeypatch, tmp_path):
    from app.core.config import Settings, get_settings

    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        FILE_CHUNK_MAX_CHARS=120,
        FILE_CHUNK_OVERLAP_CHARS=20,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.post("/v1/db/init").json()["ok"] is True
        key = _upload_text(
            client,
            content="Alpha badger context. " * 20 + "Final retrieval sentence about badgers.",
        )

        chunk_response = client.post(
            "/v1/files/chunk",
            json={"object_key": key, "max_chars": 120, "overlap_chars": 20},
        )
        assert chunk_response.status_code == 200
        chunk_body = chunk_response.json()
        assert chunk_body["ok"] is True
        assert chunk_body["db_recorded"] is True
        assert chunk_body["chunking"]["chunk_count"] > 1

        list_response = client.get("/v1/files/chunks", params={"key": key, "include_content": True})
        assert list_response.status_code == 200
        list_body = list_response.json()
        assert list_body["ok"] is True
        assert list_body["count"] == chunk_body["chunking"]["chunk_count"]

        search_response = client.get(
            "/v1/files/chunks/search",
            params={"key": key, "query": "badger retrieval", "limit": 2},
        )
        assert search_response.status_code == 200
        search_body = search_response.json()
        assert search_body["ok"] is True
        assert search_body["count"] >= 1
        assert search_body["chunks"][0]["score"] > 0
    finally:
        app.dependency_overrides.clear()


def test_chat_with_file_can_use_persisted_chunks(monkeypatch, tmp_path):
    from app.core.config import Settings, get_settings

    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
        FILE_CHUNK_MAX_CHARS=120,
        FILE_CHUNK_OVERLAP_CHARS=20,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.post("/v1/db/init").json()["ok"] is True
        key = _upload_text(
            client,
            content="Alpha project note. " * 10 + "The important retrieval animal is a badger.",
        )
        assert client.post("/v1/files/chunk", json={"object_key": key, "max_chars": 120}).json()["ok"] is True

        async def fake_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
            joined = "\n".join(message["content"] for message in payload["messages"])
            assert "[Chunk" in joined
            assert "badger" in joined.lower()
            return {
                "model": payload["model"],
                "provider": "test-provider",
                "usage": {"total_tokens": 12, "cost": 0},
                "choices": [{"message": {"content": "The retrieved chunk says badger."}, "finish_reason": "stop"}],
            }

        monkeypatch.setattr(OpenRouterClient, "chat_completion", fake_chat_completion)
        response = client.post(
            "/v1/chat/with-file",
            json={
                "object_key": key,
                "message": "What animal is important?",
                "model": "poolside/laguna-xs.2:free",
                "use_chunks": True,
                "chunk_limit": 3,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["source"]["chunked"] is True
        assert body["source"]["chunks_used"] >= 1
        assert "badger" in body["reply"].lower()
    finally:
        app.dependency_overrides.clear()


def test_chat_with_multiple_files_builds_combined_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)
    first_key = _upload_text(client, content="First file says Alpha evidence.")
    second_response = client.post(
        "/v1/files/upload-text",
        json={"filename": "second-note.txt", "content": "Second file says Beta evidence."},
    )
    assert second_response.status_code == 200
    second_key = second_response.json()["file"]["object_key"]

    async def fake_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG001
        joined = "\n".join(message["content"] for message in payload["messages"])
        assert "Attached file count: 2" in joined
        assert "First file says Alpha evidence." in joined
        assert "Second file says Beta evidence." in joined
        assert first_key not in joined
        assert second_key not in joined
        return {
            "model": payload["model"],
            "provider": "test-provider",
            "usage": {"total_tokens": 20, "cost": 0},
            "choices": [{"message": {"content": "Alpha and Beta evidence are both present."}, "finish_reason": "stop"}],
        }

    monkeypatch.setattr(OpenRouterClient, "chat_completion", fake_chat_completion)

    response = client.post(
        "/v1/chat/with-file",
        json={
            "object_key": first_key,
            "files": [
                {"lane": "uploads", "object_key": first_key, "name": "first-note.txt"},
                {"lane": "uploads", "object_key": second_key, "name": "second-note.txt"},
            ],
            "message": "Compare these files.",
            "model": "poolside/laguna-xs.2:free",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["file_count"] == 2
    assert len(body["source_citations"]) == 2
    assert body["source_citation"]["label"] == "2 attached files"
    assert "Alpha and Beta" in body["reply"]


def test_chat_with_zip_auto_chunk_strips_nul_before_sql_persistence(monkeypatch, tmp_path):
    import base64
    import io
    import zipfile

    from app.core.config import Settings, get_settings

    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
        FILE_CHUNK_MAX_CHARS=120,
        FILE_CHUNK_OVERLAP_CHARS=20,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        client = TestClient(app)
        assert client.post("/v1/db/init").json()["ok"] is True
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("src/example.txt", "alpha\x00 beta retrieval target")

        upload = client.post(
            "/v1/files/upload-base64",
            json={
                "filename": "repo.zip",
                "content_type": "application/zip",
                "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            },
        )
        assert upload.status_code == 200
        key = upload.json()["file"]["object_key"]

        dry_run = client.post(
            "/v1/chat/with-file",
            json={
                "object_key": key,
                "message": "Review the ZIP.",
                "chunk_query": "beta retrieval target",
                "model": "poolside/laguna-xs.2:free",
                "dry_run": True,
                "use_chunks": True,
                "auto_chunk": True,
                "max_file_chars": 4000,
            },
        )
        assert dry_run.status_code == 200
        body = dry_run.json()
        assert body["ok"] is True
        assert body["source"]["chunked"] is True
        assert "\x00" not in str(body)

        listed = client.get("/v1/files/chunks", params={"key": key, "include_content": True})
        assert listed.status_code == 200
        chunks = listed.json()["chunks"]
        assert chunks
        assert "\x00" not in str(chunks)
        assert any("�" in chunk.get("content", "") for chunk in chunks)
    finally:
        app.dependency_overrides.clear()

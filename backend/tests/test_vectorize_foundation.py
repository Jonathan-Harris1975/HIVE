from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.files import _vector_search_chunks
from app.core.config import Settings
from app.main import app
from app.storage.sql_store import SqlStore
from app.storage.vectorize import VectorizeClient


def test_vectorize_env_aliases_are_supported() -> None:
    settings = Settings(
        VECTORIZE_ENABLED="true",
        VECTORIZE_ACCOUNT_ID="account123",
        VECTORIZE_API_TOKEN="token123",
        VECTORIZE_INDEX_NAME="hive-chunks",
        VECTORIZE_TIMEOUT_SECONDS="9",
        VECTORIZE_MAX_ATTEMPTS="3",
        VECTORIZE_TOP_K="11",
        VECTORIZE_RETURN_METADATA="all",
        EMBEDDINGS_ENABLED="true",
        EMBEDDINGS_PROVIDER="cloudflare",
        EMBEDDINGS_MODEL="@cf/baai/bge-base-en-v1.5",
        EMBEDDINGS_DIMENSIONS="768",
    )

    assert settings.vectorize_enabled is True
    assert settings.vectorize_account_id == "account123"
    assert settings.vectorize_api_token == "token123"
    assert settings.vectorize_index_name == "hive-chunks"
    assert settings.vectorize_timeout_seconds == 9
    assert settings.vectorize_max_attempts == 3
    assert settings.vectorize_top_k == 11
    assert settings.embeddings_enabled is True
    assert settings.embeddings_model == "@cf/baai/bge-base-en-v1.5"
    assert settings.embeddings_dimensions == 768


def test_vectorize_client_disabled_without_gate() -> None:
    settings = Settings(
        VECTORIZE_ENABLED="false",
        VECTORIZE_ACCOUNT_ID="account123",
        VECTORIZE_API_TOKEN="token123",
        VECTORIZE_INDEX_NAME="hive-chunks",
    )

    client = VectorizeClient(settings)

    assert client.enabled is False
    assert client.safe_config["api_token_configured"] is True
    assert client.safe_config["index_name"] == "hive-chunks"


def test_vectorize_diagnostics_endpoint_is_safe_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.get("/v1/vectorize/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["build"] == "v1.14-execution-review-queue"
    assert body["sql_chunks_source_of_truth"] is True
    assert body["vectorize"]["enabled"] is False


@pytest.mark.asyncio
async def test_vector_search_falls_back_to_sql_chunks_when_vectorize_disabled(tmp_path) -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{tmp_path / 'hive.sqlite3'}",
        VECTORIZE_ENABLED=False,
        EMBEDDINGS_ENABLED=False,
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    result = store.record_file_chunks(
        object_key="uploads/demo.txt",
        chunks=[
            {
                "chunk_index": 0,
                "content": "Deployment failure handling should return structured diagnostics.",
                "char_start": 0,
                "char_end": 64,
                "token_estimate": 12,
                "content_sha256": "sha-demo",
            }
        ],
        source_metadata={"storage": "local"},
    )
    assert result["ok"] is True

    search = await _vector_search_chunks(
        query="deployment failure diagnostics",
        object_key="uploads/demo.txt",
        limit=3,
        settings=settings,
        fallback_sql=True,
    )

    assert search["ok"] is True
    assert search["retrieval_mode"] == "sql_fallback"
    assert search["retrieval_source"] == "sql_fallback"
    assert search["fallback_used"] is True
    assert search["vector_hits"] == 0
    assert search["sql_fallback_hits"] == 1
    assert search["count"] == 1
    assert "Deployment failure" in search["chunks"][0]["content"]

@pytest.mark.asyncio
async def test_vectorize_upsert_uses_cloudflare_vectors_multipart_part(monkeypatch) -> None:
    settings = Settings(
        VECTORIZE_ENABLED="true",
        VECTORIZE_ACCOUNT_ID="account123",
        VECTORIZE_API_TOKEN="token123",
        VECTORIZE_INDEX_NAME="hive-chunks",
    )
    client = VectorizeClient(settings)
    captured: dict[str, object] = {}

    async def fake_request(method: str, url: str, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["files"] = kwargs.get("files")
        return {"ok": True, "status_code": 200, "result": {"mutationId": "mut-test"}}

    monkeypatch.setattr(client, "_request", fake_request)
    result = await client.upsert_vectors([
        {"id": "chunk-1", "values": [0.1, 0.2, 0.3], "metadata": {"object_key": "uploads/demo.txt"}}
    ])

    assert result["ok"] is True
    files = captured["files"]
    assert isinstance(files, dict)
    assert "vectors" in files
    assert "body" not in files
    filename, payload, content_type = files["vectors"]
    assert filename == "vectors.ndjson"
    assert content_type == "application/x-ndjson"
    assert b'"id": "chunk-1"' in payload

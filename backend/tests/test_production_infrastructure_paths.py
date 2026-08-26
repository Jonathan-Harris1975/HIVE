from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.config import Settings
from app.services import repository_pipeline, service_lifecycle
from app.services.repository_manager import RepositoryManifest
from app.storage import d1, vectorize
from app.storage.r2 import R2Storage


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "aims_health_url": "https://aims.test/health",
        "rams_health_url": "https://rams.test/health",
        "koyeb_token": "test-token",
        "koyeb_service_id_aims": "svc-aims",
        "koyeb_service_id_rams": "svc-rams",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _manifest() -> RepositoryManifest:
    return RepositoryManifest(
        repository_id="repo-1",
        source_filename="repo.zip",
        fingerprint="abc123",
        file_count=4,
        total_bytes=1024,
        languages={"Python": 4},
        dependencies=[],
        created_at=1.0,
        updated_at=1.0,
        indexed_version=1,
    )


@pytest.mark.asyncio
async def test_service_lifecycle_returns_immediately_when_already_healthy() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://aims.test/health"
        return httpx.Response(200, json={"ok": True})

    events: list[dict[str, object]] = []

    async def progress(event: dict[str, object]) -> None:
        events.append(event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await service_lifecycle.ensure_service_ready(
            _settings(), "AIMS", client=client, on_progress=progress
        )

    assert result.ready is True
    assert result.already_online is True
    assert result.attempts[0]["phase"] == "initial-check"
    assert events == [{"phase": "already-online", "repo": "AIMS"}]


@pytest.mark.asyncio
async def test_service_lifecycle_resumes_then_polls_until_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter([503, 200])

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(responses), json={"ok": True})

    async def fake_resume(_client: httpx.AsyncClient, _settings: Settings, repo: str) -> dict[str, object]:
        assert repo == "RAMS"
        return {"ok": True, "transport": "koyeb_api"}

    monkeypatch.setattr(service_lifecycle, "request_service_resume", fake_resume)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await service_lifecycle.ensure_service_ready(
            _settings(),
            "RAMS",
            client=client,
            timeout_seconds=2,
            poll_interval_seconds=0,
        )

    assert result.ready is True
    assert result.already_online is False
    assert [item["phase"] for item in result.attempts] == ["initial-check", "poll"]
    assert result.mast_resume_response == {"ok": True, "transport": "koyeb_api"}


def test_service_lifecycle_rejects_unknown_service() -> None:
    with pytest.raises(service_lifecycle.UnknownServiceError):
        service_lifecycle._health_url_for(_settings(), "UNKNOWN")
    with pytest.raises(service_lifecycle.UnknownServiceError):
        service_lifecycle._service_id_for(_settings(), "UNKNOWN")


@pytest.mark.asyncio
async def test_repository_pipeline_reports_ready_when_optional_stage_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository_pipeline, "_seed_repository_memory", lambda *_: {"ok": True})
    monkeypatch.setattr(repository_pipeline, "_run_intelligence", lambda *_: {"ok": True, "qa_score": 1.0, "council_score": 1.0})

    async def skipped_search(*_args: object) -> dict[str, object]:
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    monkeypatch.setattr(repository_pipeline, "_index_in_ai_search", skipped_search)
    result = await repository_pipeline.run_repository_pipeline(
        _settings(), _manifest(), r2_persisted=True
    )

    assert result["status"] == "ready"
    assert result["r2_persisted"] is True
    assert "failed_stages" not in result


@pytest.mark.asyncio
async def test_repository_pipeline_surfaces_failed_stage_without_losing_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository_pipeline, "_seed_repository_memory", lambda *_: {"ok": True})
    monkeypatch.setattr(repository_pipeline, "_run_intelligence", lambda *_: {"ok": False, "error": "intelligence failed"})

    async def indexed(*_args: object) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(repository_pipeline, "_index_in_ai_search", indexed)
    result = await repository_pipeline.run_repository_pipeline(
        _settings(), _manifest(), r2_persisted=True
    )

    assert result["status"] == "setup_incomplete"
    assert result["required_stages_ready"] is False
    assert result["failed_stages"] == ["intelligence"]
    assert result["repository_id"] == "repo-1"


@pytest.mark.asyncio
async def test_repository_pipeline_treats_ai_search_failure_as_optional_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository_pipeline, "_seed_repository_memory", lambda *_: {"ok": True})
    monkeypatch.setattr(repository_pipeline, "_run_intelligence", lambda *_: {"ok": True, "qa_score": 1.0, "council_score": 1.0})

    async def unavailable_search(*_args: object) -> dict[str, object]:
        return {"ok": False, "error": "AI Search temporarily unavailable"}

    monkeypatch.setattr(repository_pipeline, "_index_in_ai_search", unavailable_search)
    result = await repository_pipeline.run_repository_pipeline(
        _settings(), _manifest(), r2_persisted=True
    )

    assert result["status"] == "ready_with_warnings"
    assert result["required_stages_ready"] is True
    assert result["failed_stages"] == ["ai_search"]


def test_d1_query_retries_transient_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        d1_enabled=True,
        d1_account_id="account",
        d1_database_id="database",
        d1_api_key="token",
        d1_max_attempts=2,
    )
    responses = iter([
        httpx.Response(503, json={"success": False, "errors": [{"message": "busy"}]}),
        httpx.Response(200, json={"success": True, "result": [{"results": [{"id": "1"}]}]}),
    ])

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return next(responses)

    monkeypatch.setattr(d1.httpx, "Client", FakeClient)
    monkeypatch.setattr(d1.time, "sleep", lambda _seconds: None)
    result = d1.D1MetadataStore(settings).query("SELECT 1")

    assert result["ok"] is True
    assert result["attempt"] == 2
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_vectorize_request_retries_and_preserves_zero_score(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        vectorize_enabled=True,
        vectorize_account_id="account",
        vectorize_api_token="token",
        vectorize_index_name="index",
        vectorize_max_attempts=2,
    )
    responses = iter([
        httpx.Response(503, json={"success": False, "errors": [{"message": "retry"}]}),
        httpx.Response(200, json={"success": True, "result": {"matches": [{"id": "v1", "score": 0, "similarity": 0.9}]}}),
    ])

    class FakeAsyncClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def request(self, *_args: object, **_kwargs: object) -> httpx.Response:
            return next(responses)

    monkeypatch.setattr(vectorize.httpx, "AsyncClient", FakeAsyncClient)
    client = vectorize.VectorizeClient(settings)
    result = await client.query([0.1, 0.2])

    assert result["ok"] is True
    assert result["attempt"] == 2
    assert result["matches"] == [{"id": "v1", "score": 0, "metadata": {}}]



def _r2_storage() -> R2Storage:
    settings = _settings(
        cf_r2_endpoint_url="https://account.r2.cloudflarestorage.com",
        cf_r2_access_key_id="test-access",
        cf_r2_secret_access_key="test-secret",
        cf_r2_bucket="hive-private",
        r2_multi_bucket_max_scan_keys=100,
    )
    storage = R2Storage(settings)
    storage._client = MagicMock()
    return storage


def test_d1_metadata_crud_helpers_shape_rows_and_report_partial_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        d1_enabled=True,
        d1_account_id="account",
        d1_database_id="database",
        d1_api_key="token",
    )
    store = d1.D1MetadataStore(settings)
    calls: list[tuple[str, list[object]]] = []

    def fake_query(sql: str, params: list[object] | None = None) -> dict[str, object]:
        calls.append((sql, list(params or [])))
        if sql.lstrip().startswith("SELECT"):
            return {
                "ok": True,
                "result": [{"results": [{
                    "id": "item-1",
                    "lane": "podcast",
                    "source_type": "episode",
                    "source_id": "ep-1",
                    "title": "Episode",
                    "url": "https://example.test/episode",
                    "metadata_json": '{"quality": 9}',
                    "created_at": "2026-08-22T00:00:00Z",
                    "updated_at": "2026-08-22T00:00:00Z",
                }]}],
            }
        if params == ["bad"]:
            return {"ok": False, "message": "delete failed"}
        return {"ok": True}

    monkeypatch.setattr(store, "query", fake_query)
    upsert = store.upsert_metadata(
        item_id="item-1",
        lane="podcast",
        source_type="episode",
        source_id="ep-1",
        title="Episode",
        url="https://example.test/episode",
        metadata={"quality": 9},
    )
    listed = store.list_metadata(lane="podcast", limit=9999)
    searched = store.search_metadata(query="Episode", limit=0)
    deleted = store.delete_metadata_ids(["item-1", " ", "bad"])

    assert upsert["ok"] is True
    assert listed["count"] == 1 and listed["items"][0]["metadata"] == {"quality": 9}
    assert searched["count"] == 1 and searched["items"][0]["metadata"] == {"quality": 9}
    assert deleted["ok"] is False
    assert deleted["deleted_ids"] == ["item-1"]
    assert deleted["failed"][0]["id"] == "bad"
    assert any(params[-1] == 500 for sql, params in calls if sql.lstrip().startswith("SELECT") and params)


def test_d1_schema_and_diagnostics_fail_closed_and_count_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    disabled = d1.D1MetadataStore(_settings())
    assert disabled.init_schema()["enabled"] is False
    assert disabled.ping_write()["ok"] is False

    settings = _settings(
        d1_enabled=True,
        d1_account_id="account",
        d1_database_id="database",
        d1_api_key="token",
    )
    store = d1.D1MetadataStore(settings)
    responses = iter([
        {"ok": True}, {"ok": True}, {"ok": True},  # schema
        {"ok": True}, {"ok": True},  # write probe insert/delete
        {"ok": True},  # diagnostics SELECT 1
        {"ok": True, "result": [{"results": [{"count": 12}]}]},  # count
    ])
    monkeypatch.setattr(store, "query", lambda *_args, **_kwargs: next(responses))

    assert store.init_schema()["ok"] is True
    assert store.ping_write()["ok"] is True
    diagnostics = store.diagnostics()
    assert diagnostics["ok"] is True
    assert diagnostics["schema_ready"] is True
    assert diagnostics["table_counts"]["counts"]["hive_ecosystem_metadata"] == 12

    broken = d1.D1MetadataStore(settings)
    broken_responses = iter([
        {"ok": True},
        {"ok": False, "message": "no such table: hive_ecosystem_metadata"},
    ])
    monkeypatch.setattr(broken, "query", lambda *_args, **_kwargs: next(broken_responses))
    broken_diagnostics = broken.diagnostics()
    assert broken_diagnostics["ok"] is False
    assert broken_diagnostics["schema_ready"] is False


def test_r2_list_page_searches_across_pages_and_preserves_cursor() -> None:
    storage = _r2_storage()
    storage._client.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "docs/ignore.txt", "Size": 1}],
            "CommonPrefixes": [{"Prefix": "docs/a/"}],
            "IsTruncated": True,
            "NextContinuationToken": "next-1",
        },
        {
            "Contents": [{
                "Key": "docs/match-report.json",
                "Size": 42,
                "ETag": '"etag-1"',
                "LastModified": datetime(2026, 8, 22, tzinfo=timezone.utc),
                "StorageClass": "STANDARD",
            }],
            "CommonPrefixes": [{"Prefix": "docs/a/"}, {"Prefix": "docs/b/"}],
            "IsTruncated": False,
        },
    ]

    page = storage.list_objects_page(
        prefix="docs/",
        limit=10,
        search="match",
        public_base_url="https://cdn.example.test",
    )

    assert [item.key for item in page.objects] == ["docs/match-report.json"]
    assert page.objects[0].etag == "etag-1"
    assert page.objects[0].public_url == "https://cdn.example.test/docs/match-report.json"
    assert page.prefixes == ["docs/a/", "docs/b/"]
    assert page.scanned_count == 2
    assert page.truncated is False
    assert storage._client.list_objects_v2.call_count == 2


def test_r2_metadata_read_stream_and_delete_paths() -> None:
    storage = _r2_storage()
    modified = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
    storage._client.head_object.return_value = {
        "ContentLength": 5,
        "ContentType": "text/plain",
        "LastModified": modified,
        "ETag": '"abc"',
        "CacheControl": "no-store",
        "Metadata": {"source": "test"},
    }
    storage._client.get_object.return_value = {
        "Body": BytesIO(b"hello"),
        "ContentLength": 5,
        "ContentType": "text/plain",
        "LastModified": modified,
        "ETag": '"abc"',
    }
    storage._client.delete_objects.return_value = {
        "Deleted": [{"Key": "a.txt"}],
        "Errors": [{"Key": "b.txt", "Code": "AccessDenied", "Message": "denied"}],
    }

    metadata = storage.head_object("a.txt", public_base_url="https://cdn.example.test")
    read = storage.read_object("a.txt", 10)
    stream = storage.open_object("a.txt", max_bytes=10)
    deleted = storage.delete_objects(["/a.txt", "b.txt"])

    assert metadata.size_bytes == 5 and metadata.metadata == {"source": "test"}
    assert read.content == b"hello" and read.etag == "abc"
    assert stream.size_bytes == 5 and stream.content_type == "text/plain"
    assert deleted["ok"] is False
    assert deleted["deleted_keys"] == ["a.txt"]
    assert deleted["errors"][0]["code"] == "AccessDenied"


def test_r2_read_and_stream_reject_oversized_objects() -> None:
    storage = _r2_storage()
    storage._client.head_object.return_value = {"ContentLength": 20}
    with pytest.raises(ValueError, match="max read size"):
        storage.read_object("large.bin", 10)

    body = MagicMock()
    storage._client.get_object.return_value = {"Body": body, "ContentLength": 20}
    with pytest.raises(ValueError, match="max download size"):
        storage.open_object("large.bin", max_bytes=10)
    body.close.assert_called_once()


def test_repository_pipeline_stage_helpers_capture_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import repository_intelligence, repository_manager, repository_memory, repository_profile

    captured: dict[str, object] = {}
    monkeypatch.setattr(d1, "D1MetadataStore", lambda _settings: "store")
    monkeypatch.setattr(
        repository_memory,
        "set_memory_field",
        lambda store, **kwargs: captured.update({"memory_store": store, "memory": kwargs}),
    )
    monkeypatch.setattr(repository_manager, "get_repository", lambda _repo: SimpleNamespace())
    monkeypatch.setattr(
        repository_profile,
        "build_repository_memory_profile",
        lambda _record: {
            "architecture_summary": {"status": "detected"},
            "coding_standards": {"status": "detected"},
            "build_profile": {"status": "detected"},
            "deployment_profile": {"status": "detected"},
            "environment_schema": {"status": "detected"},
        },
    )
    monkeypatch.setattr(
        repository_intelligence,
        "run_repository_intelligence",
        lambda *_args, **_kwargs: {
            "summary": {
                "status": "review_recommended",
                "finding_count": 2,
                "blocking_finding_count": 0,
                "qa_score": 0.91,
                "council_score": 0.94,
            },
            "qa": {"score": 0.91},
            "council": {"overall_score": 0.94},
            "project_dna": {"latest_qa_score": 0.91},
        },
    )

    seed = repository_pipeline._seed_repository_memory(_settings(), _manifest())
    assert seed["ok"] is True
    assert set(seed["fields_populated"]) == {
        "project_manifest",
        "architecture_summary",
        "coding_standards",
        "build_profile",
        "deployment_profile",
        "environment_schema",
    }
    intelligence = repository_pipeline._run_intelligence(_settings(), "repo-1")
    assert intelligence["ok"] is True
    assert intelligence["qa_score"] == 0.91
    assert intelligence["council_score"] == 0.94
    assert intelligence["finding_count"] == 2


@pytest.mark.asyncio
async def test_repository_pipeline_ai_search_stage_reports_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.storage import ai_search

    class FakeSearch:
        enabled = True
        def __init__(self, _settings: Settings) -> None:
            pass
        async def diagnostics(self) -> dict[str, object]:
            return {"ok": True, "provider": "cloudflare"}

    monkeypatch.setattr(ai_search, "AiSearchClient", FakeSearch)
    result = await repository_pipeline._index_in_ai_search(_settings(), _manifest())
    assert result == {"ok": True, "diagnostics": {"ok": True, "provider": "cloudflare"}}


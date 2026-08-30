from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.storage.ai_search import AiSearchClient


def _settings() -> Settings:
    return Settings(
        ai_search_enabled=True,
        ai_search_account_id="acct-123",
        ai_search_api_token="token-123",
        ai_search_instance="hive-repositories",
        ai_search_max_attempts=1,
        ai_search_timeout_seconds=5,
        ai_search_top_k=8,
    )


@pytest.mark.asyncio
async def test_diagnostics_excludes_static_media_ai_search_sources(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/ai-search/instances'):
            return httpx.Response(200, json={
                "success": True,
                "result": [
                    {"id": "hive-repositories", "paused": False},
                    {"id": "hive-skills", "paused": False},
                    {"id": "brand-assets", "paused": True},
                    {"id": "media-index", "paused": False, "source": {"bucket_name": "blog-images"}},
                ],
                "result_info": {"count": 4, "page": 1, "per_page": 100, "total_count": 4},
            })
        if path.endswith('/hive-repositories/stats'):
            return httpx.Response(200, json={"success": True, "result": {"completed": 20, "error": 0}})
        if path.endswith('/hive-skills/stats'):
            return httpx.Response(200, json={"success": True, "result": {"completed": 227, "error": 1}})
        raise AssertionError(f"excluded AI Search source was queried: {path}")

    original = httpx.AsyncClient
    class PatchedAsyncClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)
    result = await AiSearchClient(_settings()).diagnostics()

    assert result["discovered_instance_count"] == 4
    assert result["excluded_instance_count"] == 2
    assert result["excluded_instances"] == ["brand-assets", "media-index"]
    assert result["instance_count"] == 2
    assert result["active_instance_count"] == 2
    assert result["paused_instance_count"] == 0
    assert result["indexing_error_count"] == 1
    assert result["ok"] is True
    assert result["availability_status"] == "available"
    assert result["indexing_healthy"] is False
    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_search_all_fans_out_and_tags_source_instance(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/ai-search/instances'):
            return httpx.Response(200, json={
                "success": True,
                "result": [
                    {"id": "hive-repositories", "paused": False},
                    {"id": "hive-skills", "paused": False},
                    {"id": "podcastart", "paused": False},
                    {"id": "media-index", "paused": False, "data_source": {"bucket": "blog-images"}},
                ],
            })
        if path.endswith('/hive-repositories/search'):
            body = __import__('json').loads(request.content)
            assert body["query"] == "headroom"
            assert body["ai_search_options"]["retrieval"]["max_num_results"] == 2
            assert "max_num_results" not in body
            return httpx.Response(200, json={"success": True, "result": {"data": [{"id": "a", "score": 0.8}]}})
        if path.endswith('/hive-skills/search'):
            return httpx.Response(200, json={"success": True, "result": {"data": [{"id": "b", "score": 0.9}]}})
        raise AssertionError(path)

    original = httpx.AsyncClient
    class PatchedAsyncClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)
    result = await AiSearchClient(_settings()).search_all("headroom", top_k=2)

    assert result["ok"] is True
    assert result["instance_count"] == 2
    assert [item["id"] for item in result["matches"]] == ["b", "a"]
    assert result["matches"][0]["_ai_search_instance"] == "hive-skills"


@pytest.mark.asyncio
async def test_direct_ai_search_access_to_static_media_is_rejected_without_network(monkeypatch):
    async def fail_request(*_args, **_kwargs):
        raise AssertionError("excluded AI Search source must not be queried")

    client = AiSearchClient(_settings())
    monkeypatch.setattr(client, "_request", fail_request)

    searched = await client.search("logo", instance_id="brand-assets")
    stats = await client.instance_stats("podcastart")

    assert searched["ok"] is False
    assert searched["error_code"] == "ai_search_source_excluded"
    assert stats["ok"] is False
    assert stats["error_code"] == "ai_search_source_excluded"


@pytest.mark.asyncio
async def test_list_instances_paginates_until_total_count(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        assert request.url.params.get("per_page") == "100"
        if page == 1:
            rows = [{"id": f"index-{idx}", "paused": False} for idx in range(100)]
        elif page == 2:
            rows = [{"id": "index-100", "paused": False}]
        else:
            raise AssertionError(f"unexpected page {page}")
        return httpx.Response(200, json={
            "success": True,
            "result": rows,
            "result_info": {"count": len(rows), "page": page, "per_page": 100, "total_count": 101},
        })

    original = httpx.AsyncClient
    class PatchedAsyncClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)
    result = await AiSearchClient(_settings()).list_instances()

    assert result["ok"] is True
    assert result["count"] == 101
    assert result["instances"][-1]["id"] == "index-100"

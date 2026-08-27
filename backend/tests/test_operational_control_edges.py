from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.api import buckets, monthly_review, repositories, service_actions
from app.services import koyeb_control, model_sync
from app.services.service_lifecycle import ServiceResumeError, ServiceWakeTimeout, WakeResult


@pytest.mark.asyncio
async def test_koyeb_action_validates_configuration_and_action() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        with pytest.raises(koyeb_control.KoyebControlError, match="Unsupported"):
            await koyeb_control.service_action(client, token="token", service_id="svc", action="delete")
        with pytest.raises(koyeb_control.KoyebControlError, match="not configured"):
            await koyeb_control.service_action(client, token="", service_id="svc", action="resume")


@pytest.mark.asyncio
async def test_koyeb_action_and_status_map_provider_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"service": {"status": "resuming"}})
        return httpx.Response(200, json={"service": {"status": "paused"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        action = await koyeb_control.service_action(client, token=" token ", service_id=" svc ", action="RESUME")
        status = await koyeb_control.service_status(client, token="token", service_id="svc")
    assert action["ok"] is True and action["action"] == "resume"
    assert status["configured"] is True and status["status"] == "standby"


@pytest.mark.asyncio
async def test_koyeb_status_handles_missing_http_failure_and_unknown_payload() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        missing = await koyeb_control.service_status(client, token="", service_id="")
        failed = await koyeb_control.service_status(client, token="token", service_id="svc")
    assert missing["status"] == "not_configured"
    assert failed["status"] == "down" and failed["http_status"] == 503

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as client:
        unknown = await koyeb_control.service_status(client, token="token", service_id="svc")
    assert unknown["status"] == "degraded"


@pytest.mark.asyncio
async def test_model_sync_retries_transient_response_and_stops_on_permanent(monkeypatch) -> None:
    monkeypatch.setattr(model_sync.asyncio, "sleep", lambda _delay: _NoopAwaitable())
    attempts = 0

    def retry_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, json={"ok": attempts > 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(retry_handler)) as client:
        result = await model_sync._post_with_retry(client, url="https://example.test/apply", token="t", payload={}, attempts=3)
    assert result["attempt"] == 2

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(400, text="bad"))) as client:
        with pytest.raises(model_sync.ModelSyncError, match="HTTP 400"):
            await model_sync._post_with_retry(client, url="https://example.test/apply", token="t", payload={}, attempts=3)


class _NoopAwaitable:
    def __await__(self):
        if False:
            yield None
        return None


def test_model_sync_rejects_template_secrets() -> None:
    assert model_sync._usable_secret("{{ secret.AIMS_API_KEY }}") == ""
    assert model_sync._usable_secret("  real-key  ") == "real-key"


@pytest.mark.asyncio
async def test_model_sync_disabled_and_missing_configuration_fail_closed() -> None:
    disabled = SimpleNamespace(model_governance_sync_enabled=False)
    assert (await model_sync.sync_model_registry_downstream(disabled, source_run_id="r", registry={}))["enabled"] is False

    missing = SimpleNamespace(
        model_governance_sync_enabled=True,
        aims_api_key="{{ secret.AIMS_API_KEY }}",
        rams_api_key="",
        aims_base_url="",
        rams_base_url="",
    )
    with pytest.raises(model_sync.ModelSyncError, match="configuration is missing"):
        await model_sync.sync_model_registry_downstream(missing, source_run_id="r", registry={})


@pytest.mark.asyncio
async def test_pause_rams_cleanup_paths(monkeypatch) -> None:
    settings = SimpleNamespace(koyeb_token="token", koyeb_service_id_rams="svc")
    async with httpx.AsyncClient() as client:
        assert (await model_sync._pause_rams_if_woken(client, settings, None))["attempted"] is False
        online = WakeResult(repo="RAMS", ready=True, already_online=True)
        assert (await model_sync._pause_rams_if_woken(client, settings, online))["reason"] == "already-online"

        async def fail_action(*args, **kwargs):
            raise koyeb_control.KoyebControlError("provider down")

        monkeypatch.setattr(model_sync, "service_action", fail_action)
        woken = WakeResult(repo="RAMS", ready=True, already_online=False)
        failed = await model_sync._pause_rams_if_woken(client, settings, woken)
        assert failed == {"attempted": True, "ok": False, "error": "provider down"}


@pytest.mark.asyncio
async def test_bucket_catalogue_deduplicates_and_hides_internal_buckets() -> None:
    settings = SimpleNamespace(r2_ecosystem_lanes=[
        {"bucket": "blog", "lane": "blog", "configured": True, "readable": True, "writable": True, "access_mode": "read_write"},
        {"bucket": "blog", "lane": "blog-copy", "configured": True},
        {"bucket": "raw-text", "lane": "internal", "configured": True},
        {"bucket": "ebooks", "lane": "ebooks", "configured": False, "readable": False, "writable": False},
    ])
    result = await buckets.get_buckets(settings)
    assert result["count"] == 2
    assert [entry["bucket"] for entry in result["buckets"]] == ["blog", "ebooks"]


def test_repository_api_exception_helpers_are_distinct() -> None:
    not_found = repositories._not_found("repo-1")
    assert not_found.status_code == 404 and "repo-1" in str(not_found.detail)
    unavailable = repositories._workdir_unavailable(Exception("re-upload required"))
    assert unavailable.status_code == 409


def test_repository_manifest_persistence_skips_when_r2_writes_disabled(monkeypatch) -> None:
    class FakeR2:
        write_enabled = False
        def __init__(self, settings):
            pass

    monkeypatch.setattr(repositories, "R2Storage", FakeR2)
    settings = SimpleNamespace(r2_bucket_repositories="repos")
    assert repositories._persist_manifest_to_r2({"repository_id": "r1"}, settings) is False


@pytest.mark.asyncio
async def test_service_wake_ticket_records_success_and_timeout(monkeypatch) -> None:
    service_actions._WAKE_TICKETS.clear()
    service_actions._WAKE_TICKETS["ok"] = {"status": "running", "phase": "queued", "events": [], "_created_monotonic": time.monotonic()}

    async def ready(settings, repo, on_progress=None, **kwargs):
        if on_progress:
            await on_progress({"phase": "polling"})
        return WakeResult(repo=repo, ready=True, already_online=False, attempts=[{}], elapsed_seconds=1.2)

    monkeypatch.setattr(service_actions, "ensure_service_ready", ready)
    monkeypatch.setattr(service_actions, "clear_repo_health_cache", lambda: None)
    await service_actions._run_wake_ticket("ok", "AIMS", SimpleNamespace())
    assert service_actions._WAKE_TICKETS["ok"]["status"] == "ready"
    assert service_actions._WAKE_TICKETS["ok"]["result"]["attempts"] == 1

    service_actions._WAKE_TICKETS["timeout"] = {"status": "running", "phase": "queued", "events": [], "_created_monotonic": time.monotonic()}

    async def timeout(settings, repo, on_progress=None, **kwargs):
        raise ServiceWakeTimeout(repo, 10, 3)

    monkeypatch.setattr(service_actions, "ensure_service_ready", timeout)
    await service_actions._run_wake_ticket("timeout", "RAMS", SimpleNamespace())
    assert service_actions._WAKE_TICKETS["timeout"]["status"] == "timeout"


def test_service_ticket_sweep_and_lookup() -> None:
    service_actions._WAKE_TICKETS.clear()
    service_actions._WAKE_TICKETS["old"] = {"repo": "AIMS", "_created_monotonic": time.monotonic() - 4000}
    service_actions._sweep_tickets()
    assert "old" not in service_actions._WAKE_TICKETS
    with pytest.raises(HTTPException) as exc:
        import asyncio
        asyncio.run(service_actions.get_ensure_ready_status(repo="AIMS", wake_id="missing"))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_monthly_review_endpoint_maps_validation_errors(monkeypatch) -> None:
    async def invalid(settings, period=None):
        raise ValueError("period must be YYYY-MM")

    monkeypatch.setattr(monthly_review, "generate_and_archive_monthly_review", invalid)
    with pytest.raises(HTTPException) as exc:
        await monthly_review.generate_monthly_review_endpoint(period="bad", settings=SimpleNamespace())
    assert exc.value.status_code == 400


def test_monthly_review_history_and_missing_period(monkeypatch) -> None:
    monkeypatch.setattr(monthly_review, "list_monthly_reviews", lambda settings, limit=24: {"ok": True, "items": []})
    history = monthly_review.list_monthly_review_history(limit=10, settings=SimpleNamespace())
    assert history["ok"] is True
    with pytest.raises(HTTPException) as exc:
        monthly_review.get_monthly_review_by_period("2026-01", settings=SimpleNamespace())
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_repository_api_listing_manifest_diff_reindex_and_cleanup(monkeypatch) -> None:
    summary = SimpleNamespace(repository_id="repo-1", status="ready")
    monkeypatch.setattr(repositories, "list_repositories", lambda: [summary])
    monkeypatch.setattr(
        repositories,
        "_repository_memory_readiness",
        lambda settings, repository_ids: {
            "repo-1": {"memory_status": "ready", "memory_ready": True}
        },
    )
    listed = await repositories.get_repositories(settings=SimpleNamespace())
    assert listed["repositories"] == [
        {
            "repository_id": "repo-1",
            "status": "ready",
            "memory_status": "ready",
            "memory_ready": True,
        }
    ]

    manifest = SimpleNamespace(public_payload=lambda: {"repository_id": "repo-1"})
    record = SimpleNamespace(manifest=manifest)
    monkeypatch.setattr(repositories, "get_repository", lambda repository_id: record if repository_id == "repo-1" else None)
    monkeypatch.setattr(repositories, "is_rehydrated", lambda _record: True)
    payload = await repositories.get_repository_manifest("repo-1")
    assert payload == {"repository_id": "repo-1", "rehydrated": True}
    with pytest.raises(HTTPException) as exc:
        await repositories.get_repository_manifest("missing")
    assert exc.value.status_code == 404

    monkeypatch.setattr(repositories, "repository_diff", lambda repository_id: {"repository_id": repository_id, "changed": 2})
    assert (await repositories.get_repository_diff("repo-1"))["changed"] == 2
    monkeypatch.setattr(repositories, "repository_diff", lambda _repository_id: None)
    with pytest.raises(HTTPException) as exc:
        await repositories.get_repository_diff("missing")
    assert exc.value.status_code == 404

    monkeypatch.setattr(repositories, "reindex_repository", lambda _repository_id: manifest)
    monkeypatch.setattr(repositories, "_persist_manifest_to_r2", lambda payload, settings: True)
    reindexed = await repositories.post_repository_reindex("repo-1", settings=SimpleNamespace())
    assert reindexed["r2_persisted"] is True

    monkeypatch.setattr(repositories, "cleanup_repository", lambda repository_id: repository_id == "repo-1")
    monkeypatch.setattr(
        repositories,
        "_delete_repository_artifacts",
        lambda repository_id, settings: {"r2_deleted": True, "memory_deleted": True},
    )
    delete_settings = SimpleNamespace()
    assert (await repositories.delete_repository("repo-1", settings=delete_settings))["removed"] is True
    with pytest.raises(HTTPException) as exc:
        await repositories.delete_repository("missing", settings=delete_settings)
    assert exc.value.status_code == 404

    monkeypatch.setattr(repositories, "cleanup_expired_repositories", lambda ttl_seconds: ["old-1", "old-2"])
    cleaned = await repositories.post_cleanup_expired(settings=SimpleNamespace(repository_ttl_seconds=3600))
    assert cleaned == {"removed": ["old-1", "old-2"], "removed_count": 2}


@pytest.mark.asyncio
async def test_repository_api_maps_workdir_and_manager_errors(monkeypatch) -> None:
    def unavailable(_repository_id):
        raise repositories.RepositoryWorkdirUnavailableError("working copy unavailable")

    monkeypatch.setattr(repositories, "repository_diff", unavailable)
    with pytest.raises(HTTPException) as exc:
        await repositories.get_repository_diff("repo-1")
    assert exc.value.status_code == 409

    monkeypatch.setattr(repositories, "reindex_repository", unavailable)
    with pytest.raises(HTTPException) as exc:
        await repositories.post_repository_reindex("repo-1", settings=SimpleNamespace())
    assert exc.value.status_code == 409

    def missing(_repository_id):
        raise repositories.RepositoryManagerError("missing")

    monkeypatch.setattr(repositories, "reindex_repository", missing)
    with pytest.raises(HTTPException) as exc:
        await repositories.post_repository_reindex("repo-1", settings=SimpleNamespace())
    assert exc.value.status_code == 404


def test_repository_manifest_persistence_success_and_failure(monkeypatch) -> None:
    class FakeR2:
        write_enabled = True
        def __init__(self, settings):
            self.settings = settings
        def put_file(self, path, key, **kwargs):
            assert path.is_file()
            assert key == "manifests/repo-1.json"
            return {"ok": True}

    settings = SimpleNamespace(r2_bucket_repositories="repositories")
    monkeypatch.setattr(repositories, "R2Storage", FakeR2)
    assert repositories._persist_manifest_to_r2({"repository_id": "repo-1"}, settings) is True

    class BrokenR2(FakeR2):
        def put_file(self, path, key, **kwargs):
            raise OSError("storage unavailable")

    monkeypatch.setattr(repositories, "R2Storage", BrokenR2)
    assert repositories._persist_manifest_to_r2({"repository_id": "repo-1"}, settings) is False


@pytest.mark.asyncio
async def test_service_start_and_status_contract(monkeypatch) -> None:
    service_actions._WAKE_TICKETS.clear()
    with pytest.raises(HTTPException) as exc:
        await service_actions.start_ensure_ready(repo="HIVE", settings=SimpleNamespace())
    assert exc.value.status_code == 404

    created = []
    def capture_task(coro):
        created.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(service_actions.asyncio, "create_task", capture_task)
    result = await service_actions.start_ensure_ready(repo="AIMS", settings=SimpleNamespace())
    assert result["ok"] is True and result["status"] == "running"
    wake_id = result["wake_id"]
    status_payload = await service_actions.get_ensure_ready_status(repo="AIMS", wake_id=wake_id)
    assert status_payload["ticket_id"] == wake_id
    assert all(not key.startswith("_") for key in status_payload)
    with pytest.raises(HTTPException) as exc:
        await service_actions.get_ensure_ready_status(repo="RAMS", wake_id=wake_id)
    assert exc.value.status_code == 404
    assert len(created) == 1


@pytest.mark.asyncio
async def test_service_wake_ticket_maps_known_and_unexpected_failures(monkeypatch) -> None:
    for ticket_id, error in (
        ("resume", ServiceResumeError("AIMS", "resume rejected")),
        ("unexpected", RuntimeError("boom")),
    ):
        service_actions._WAKE_TICKETS[ticket_id] = {
            "status": "running", "phase": "queued", "events": [], "_created_monotonic": time.monotonic()
        }

        async def fail(*args, _error=error, **kwargs):
            raise _error

        monkeypatch.setattr(service_actions, "ensure_service_ready", fail)
        await service_actions._run_wake_ticket(ticket_id, "AIMS", SimpleNamespace())
        assert service_actions._WAKE_TICKETS[ticket_id]["status"] == "failed"
        assert service_actions._WAKE_TICKETS[ticket_id]["finished_at"] > 0


@pytest.mark.asyncio
async def test_service_proxy_validates_configuration_and_forwards(monkeypatch) -> None:
    body = service_actions.ProxyRequest(method="POST", path="health/check", json_body={"probe": True})
    with pytest.raises(HTTPException) as exc:
        await service_actions.ensure_ready_and_proxy(body=body, repo="HIVE", settings=SimpleNamespace())
    assert exc.value.status_code == 404

    missing = SimpleNamespace(aims_health_url="", rams_health_url="")
    with pytest.raises(HTTPException) as exc:
        await service_actions.ensure_ready_and_proxy(body=body, repo="AIMS", settings=missing)
    assert exc.value.status_code == 503

    requested = {}
    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'
        def json(self):
            return {"ok": True, "source": "AIMS"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def request(self, method, url, json=None):
            requested.update(method=method, url=url, json=json)
            return FakeResponse()

    async def ready(*args, **kwargs):
        return WakeResult(repo="AIMS", ready=True, already_online=True)

    monkeypatch.setattr(service_actions.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(service_actions, "ensure_service_ready", ready)
    monkeypatch.setattr(service_actions, "clear_repo_health_cache", lambda: None)
    settings = SimpleNamespace(aims_health_url="https://app.example.test/health", rams_health_url="https://rams.example.test/health")
    result = await service_actions.ensure_ready_and_proxy(body=body, repo="AIMS", settings=settings)
    assert result == {"ok": True, "http_status": 200, "body": {"ok": True, "source": "AIMS"}}
    assert requested == {"method": "POST", "url": "https://app.example.test/health/check", "json": {"probe": True}}


def test_monthly_review_fetches_archive_and_maps_archive_errors(monkeypatch) -> None:
    missing_archive = {
        "ok": True,
        "items": [{"source_id": "2026-07", "metadata": {}}],
    }
    monkeypatch.setattr(monthly_review, "list_monthly_reviews", lambda settings, limit=200: missing_archive)
    with pytest.raises(HTTPException) as exc:
        monthly_review.get_monthly_review_by_period("2026-07", settings=SimpleNamespace())
    assert exc.value.status_code == 404

    indexed = {
        "ok": True,
        "items": [{"source_id": "2026-07", "metadata": {"r2_object": {"key": "monthly/2026-07.json", "bucket": "reviews"}}}],
    }
    monkeypatch.setattr(monthly_review, "list_monthly_reviews", lambda settings, limit=200: indexed)

    class FakeR2:
        def __init__(self, settings):
            pass
        def read_object(self, key, **kwargs):
            assert key == "monthly/2026-07.json"
            return SimpleNamespace(content=b'{"period":"2026-07","ok":true}')

    monkeypatch.setattr(monthly_review, "R2Storage", FakeR2)
    report = monthly_review.get_monthly_review_by_period("2026-07", settings=SimpleNamespace())
    assert report["period"] == "2026-07" and report["ok"] is True

    class BrokenR2(FakeR2):
        def read_object(self, key, **kwargs):
            return SimpleNamespace(content=b'not-json')

    monkeypatch.setattr(monthly_review, "R2Storage", BrokenR2)
    with pytest.raises(HTTPException) as exc:
        monthly_review.get_monthly_review_by_period("2026-07", settings=SimpleNamespace())
    assert exc.value.status_code == 502

    monkeypatch.setattr(monthly_review, "list_monthly_reviews", lambda settings, limit=200: {"ok": False, "error": "d1 unavailable"})
    assert monthly_review.get_monthly_review_by_period("2026-07", settings=SimpleNamespace())["ok"] is False


def test_repository_memory_readiness_requires_profile_and_persisted_intelligence(monkeypatch) -> None:
    class FakeStore:
        enabled = True

        def __init__(self, _settings):
            pass

        def list_metadata(self, *, lane, limit):
            assert lane == repositories.LANE
            values = {
                **{field: {"generated": True} for field in repositories.SCALAR_FIELDS},
                "qa_history": [{"repository_id": "HIVE", "score": 1.0}],
                "repository_council_history": [{"repository_id": "HIVE", "overall_score": 1.0}],
                "repository_intelligence_history": [
                    {
                        "repository_id": "HIVE",
                        "repository_context": {"repository_id": "HIVE", "fingerprint": "hive-fingerprint"},
                    }
                ],
            }
            return {
                "ok": True,
                "items": [
                    {
                        "source_id": "HIVE",
                        "source_type": field,
                        "metadata": {"value": value},
                    }
                    for field, value in values.items()
                ],
            }

    monkeypatch.setattr(repositories, "D1MetadataStore", FakeStore)
    hive_record = SimpleNamespace(manifest=SimpleNamespace(fingerprint="hive-fingerprint"))
    monkeypatch.setattr(repositories, "get_repository", lambda repository_id: hive_record if repository_id == "HIVE" else None)
    result = repositories._repository_memory_readiness(SimpleNamespace(), ["HIVE", "AIMS"])

    assert result["HIVE"]["memory_status"] == "ready"
    assert result["HIVE"]["profile_ready"] is True
    assert result["HIVE"]["intelligence_ready"] is True
    assert result["HIVE"]["memory_ready"] is True
    assert result["AIMS"]["memory_status"] == "empty"
    assert result["AIMS"]["memory_ready"] is False

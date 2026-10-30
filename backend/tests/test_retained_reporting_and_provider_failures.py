"""The retained operational checks work without a model Council or promotion."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from datetime import datetime, UTC

import httpx
import pytest
from fastapi import HTTPException

from app.api import monthly_review as api
from app import monthly_governance_worker as worker
from app.core.config import Settings
from app.services import monthly_review as reports
from app.services.providers import openrouter_provider as providers
from app.services.providers.base import parse_provider_model, time_call


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload,healthy", [
    (200, {"data": [{"id": "approved/model"}]}, True),
    (401, {"error": "invalid authentication"}, False),
    (200, {"data": ["invalid-row"]}, False),
    (200, {"unexpected": []}, False),
])
async def test_primary_provider_health_checks_real_account_availability(monkeypatch, status, payload, healthy):
    real_client = httpx.AsyncClient
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs))
    provider = providers.OpenRouterProvider(Settings(_env_file=None, OPENROUTER_API_KEY="test-provider-key"))

    async def catalog(**kwargs):
        return [{"id": "approved/model"}, {"id": "unavailable/model"}]

    monkeypatch.setattr(provider._client, "list_models", catalog)
    report = await provider.health()
    assert report.ok is healthy
    assert seen[0].url.path.endswith("/models/user")
    assert seen[0].headers["authorization"] == "Bearer test-provider-key"
    if healthy:
        models = await provider.list_models()
        assert [model.available for model in models] == [True, False]
    else:
        assert report.error


@pytest.mark.asyncio
async def test_compatible_provider_unusable_payload_and_outage_are_reported():
    provider = providers.OpenRouterCompatibleProvider(name="test", base_url="https://provider.example", api_token="",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": "invalid"})))
    assert await provider.list_models() == []
    provider._transport = httpx.MockTransport(lambda request: httpx.Response(503))
    assert (await provider.health()).ok is False


@pytest.mark.asyncio
async def test_invalid_catalog_numbers_are_not_invented_and_timing_preserves_result():
    model = parse_provider_model({"id": "test/model", "context_length": "invalid", "pricing": {"prompt": "bad", "completion": {}}})
    assert model.context_length is None

    async def value():
        return "same result"

    result, elapsed = await time_call(value())
    assert result == "same result"
    assert elapsed >= 0


@pytest.mark.parametrize("record,code", [({"ok": False}, 503), ({"ok": True, "found": False}, 404)])
def test_monthly_status_lookup_preserves_missing_or_unavailable_state(monkeypatch, record, code):
    monkeypatch.setattr(api, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(api, "get_job", lambda store, **kwargs: record)
    with pytest.raises(HTTPException) as raised:
        api.monthly_review_job_status("2026-09", Settings(_env_file=None))
    assert raised.value.status_code == code
    with pytest.raises(HTTPException) as raised:
        api.monthly_review_job_status("bad-period", Settings(_env_file=None))
    assert raised.value.status_code == 400


@pytest.mark.asyncio
async def test_background_report_failure_retains_claim_and_completed_report_records_status(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "D1MetadataStore", lambda settings: object())

    async def report(settings, **kwargs):
        return {"ok": False}

    def complete(store, **kwargs):
        calls.append(kwargs)
        return {"ok": False, "completed": False}

    monkeypatch.setattr(api, "generate_and_archive_monthly_review", report)
    monkeypatch.setattr(api, "complete_job", complete)
    await api._run_monthly_review_job(Settings(_env_file=None), "2026-09", "owner")
    assert calls[0]["succeeded"] is False

    async def interrupted(settings, **kwargs):
        raise RuntimeError("report archive unavailable")

    monkeypatch.setattr(api, "generate_and_archive_monthly_review", interrupted)
    with pytest.raises(RuntimeError):
        await api._run_monthly_review_job(Settings(_env_file=None), "2026-09", "owner")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_report_archive_and_section_failures_stay_visible(monkeypatch):
    class BrokenStorage:
        def __init__(self, settings):
            raise RuntimeError("archive unavailable")

    settings = Settings(_env_file=None)
    monkeypatch.setattr(reports, "R2Storage", BrokenStorage)
    assert reports._write_report_to_r2(settings, {"period": "2026-09", "report_id": "r"})["ok"] is False
    monkeypatch.setattr(reports, "D1MetadataStore", lambda settings: SimpleNamespace(enabled=False))
    assert reports._index_report_in_d1(settings, {}, None)["ok"] is False
    assert reports.list_monthly_reviews(settings)["ok"] is False
    assert reports._normalise_section_result({"ok": False, "reason": "unavailable"})["ok"] is False

    async def fail():
        raise RuntimeError("provider offline")

    assert (await reports._async_section(fail()))["error_type"] == "RuntimeError"


def test_previous_month_rollover_and_report_worker_cli(monkeypatch):
    class January(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 12, tzinfo=UTC)

    monkeypatch.setattr(reports, "datetime", January)
    assert reports._period_bounds(None)[0] == "2025-12"
    seen = []

    async def execute(period, **kwargs):
        seen.append((period, kwargs))
        return 0

    monkeypatch.setattr(worker, "execute", execute)
    monkeypatch.setattr(sys, "argv", ["report-worker", "--previous-month", "--preflight-only"])
    assert worker.main() == 0
    assert seen[0][1]["preflight_only"] is True


def test_manual_override_validation_and_retirement_notices_remain_advisory():
    from datetime import timedelta
    from app.services.model_governance import ModelUseJustification, is_premium_model, lifecycle_status, validate_justification

    settings = Settings(_env_file=None)
    assert is_premium_model("", settings) is False
    valid, issues = validate_justification(ModelUseJustification(), settings)
    assert valid is False
    assert "missing_expected_calls" in issues
    assert "missing_expected_input_tokens" in issues
    assert "missing_expected_output_tokens" in issues
    assert "missing_estimated_cost_usd" in issues
    assert "missing_expires_at" in issues
    approval = ModelUseJustification(expires_at=datetime.now() + timedelta(days=settings.model_governance_approval_max_days + 2))
    assert "approval_exceeds_maximum_duration" in validate_justification(approval, settings)[1]
    assert lifecycle_status("not-a-date")[0] == "watch"
    assert lifecycle_status("2099-01-01", now=datetime(2026, 10, 29, tzinfo=UTC))[0] == "active"


@pytest.mark.parametrize('outputs,selectable', [(['text'], True), (['image'], False), (['video'], False), (['speech'], True), (['embeddings'], True), ([], True)])
def test_manual_catalogue_selection_keeps_modality_restrictions(outputs, selectable):
    from app.services.model_router import ModelRouter
    router = ModelRouter(Settings(_env_file=None))
    assert router._chat_selection_policy(outputs)[0] is selectable


def test_manual_catalogue_groups_capabilities_without_changing_defaults():
    from app.services.model_router import ModelRouter
    router = ModelRouter(Settings(_env_file=None))
    before = router.select_title_model()
    groups = router._model_groups(model_id='test/coder', name='Reasoning', description='',
        context_length='100000', input_modalities=['file', 'image', 'video'],
        output_modalities=['text', 'image', 'video', 'audio'], supported_parameters=['reasoning'],
        configured=True, is_free=True)
    for group in ['configured', 'free', 'image_generation', 'video_generation', 'audio', 'video_analysis', 'vision', 'documents', 'reasoning', 'coding']:
        assert group in groups
    manifest = router.model_group_manifest([{'primary_group': group, 'chat_selectable': True} for group in groups])
    assert len(manifest) == len(groups)
    assert all(item['count'] == item['chat_selectable_count'] == 1 for item in manifest)
    assert router._model_groups(model_id='unknown', name='', description='', context_length=None,
        input_modalities=[], output_modalities=[], supported_parameters=[], configured=False, is_free=False) == ['other']
    assert router._modalities_from_arrow('text+image -> text+audio') == (['text', 'image'], ['text', 'audio'])
    assert router._modalities_from_arrow(None) == ([], [])
    assert router._int_value('bad') == 0
    assert router._float_value('bad') == float('inf')
    assert router._is_free_model('test/paid', {'prompt': 'bad'}) is False
    assert router.select_title_model() == before


@pytest.mark.asyncio
@pytest.mark.parametrize('probe,expected', [({'ok': False}, 6), ({'ok': True, 'schema_ready': True}, 0)])
async def test_report_worker_preflight_schema_failure_is_visible(monkeypatch, probe, expected):
    monkeypatch.setattr(worker, 'get_settings', lambda: object())
    monkeypatch.setattr(worker, 'check_readiness', lambda settings: [])
    monkeypatch.setattr(worker, 'D1MetadataStore', lambda settings: SimpleNamespace(diagnostics=lambda: probe))
    assert await worker.execute('2026-09', preflight_only=True) == expected


@pytest.mark.asyncio
async def test_report_worker_rejects_missing_configuration_and_unpersisted_completion(monkeypatch):
    monkeypatch.setattr(worker, 'get_settings', lambda: object())
    monkeypatch.setattr(worker, 'check_readiness', lambda settings: ['R2_BUCKET_AUDITS'])
    assert await worker.execute('2026-09') == 5
    assert await worker.execute('2099-01') == 7
    monkeypatch.setattr(worker, 'check_readiness', lambda settings: [])
    monkeypatch.setattr(worker, 'D1MetadataStore', lambda settings: object())
    monkeypatch.setattr(worker, 'claim_job', lambda *args, **kwargs: {'ok': True, 'claimed': True, 'job_id': 'report'})
    async def report(*args, **kwargs):
        return {'ok': False}
    monkeypatch.setattr(worker, 'generate_and_archive_monthly_review', report)
    monkeypatch.setattr(worker, 'complete_job', lambda *args, **kwargs: {'ok': False})
    assert await worker.execute('2026-09') == 4


@pytest.mark.asyncio
async def test_additional_provider_health_retains_authentication_and_catalogue_count():
    from app.services.providers.registry import _parse_extra_providers
    assert _parse_extra_providers('bad-json') == []
    assert _parse_extra_providers('{}') == []
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={'data': [{'id': 'test/model'}]})
    provider = providers.OpenRouterCompatibleProvider(name='test', base_url='https://provider.example', api_token='test-token', transport=httpx.MockTransport(handle))
    health = await provider.health()
    assert health.ok and health.model_count == 1
    assert seen[0].headers['authorization'] == 'Bearer test-token'


@pytest.mark.asyncio
async def test_monthly_api_duplicate_claim_does_not_schedule_a_second_report(monkeypatch):
    from fastapi import BackgroundTasks
    monkeypatch.setattr(api, 'D1MetadataStore', lambda settings: object())
    monkeypatch.setattr(api, 'claim_job', lambda *args, **kwargs: {'ok': True, 'claimed': False})
    monkeypatch.setattr(api, 'get_job', lambda *args, **kwargs: {'ok': True, 'found': True, 'status': 'claimed'})
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        await api.generate_monthly_review_endpoint(tasks, '2026-09', Settings(_env_file=None))
    assert raised.value.status_code == 409
    assert tasks.tasks == []
    assert api.monthly_review_job_status('2026-09', Settings(_env_file=None))['status'] == 'claimed'
    with pytest.raises(ValueError):
        reports._period_bounds('2026-99')


def test_operator_modes_keep_existing_task_routes():
    from app.services.model_router import ModelRouter, TaskType
    router = ModelRouter(Settings(_env_file=None))
    assert router.classify_task('review python traceback') == TaskType.CODE
    assert router.classify_task('please recap the discussion') == TaskType.SUMMARY

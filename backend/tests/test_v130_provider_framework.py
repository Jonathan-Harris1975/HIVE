from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.services.providers.base import parse_provider_model
from app.services.providers.openrouter_provider import OpenRouterCompatibleProvider
from app.services.providers.registry import _parse_extra_providers, discover_providers


def test_parse_provider_model_extracts_capabilities():
    raw = {
        "id": "acme/coder-9000",
        "name": "Acme Coder 9000",
        "context_length": 128000,
        "pricing": {"prompt": "0.000002", "completion": "0.000008"},
        "supported_parameters": ["tools", "response_format"],
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
    }

    model = parse_provider_model(raw)

    assert model.model_id == "acme/coder-9000"
    assert model.context_length == 128000
    assert model.pricing_prompt == pytest.approx(0.000002)
    assert model.supports_tools is True
    assert model.supports_structured_output is True
    assert model.input_modalities == ("text",)


def test_parse_provider_model_handles_missing_fields_gracefully():
    model = parse_provider_model({"id": "bare-model"})

    assert model.model_id == "bare-model"
    assert model.context_length is None
    assert model.pricing_prompt is None
    assert model.supports_tools is False
    assert model.supports_structured_output is False


@pytest.mark.asyncio
async def test_openrouter_compatible_provider_list_models_via_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "m1", "name": "Model One", "supported_parameters": ["tools"]},
                    {"id": "m2", "name": "Model Two"},
                ]
            },
        )

    provider = OpenRouterCompatibleProvider(
        name="acme",
        base_url="https://acme.example/api/v1",
        api_token="test-token",
        transport=httpx.MockTransport(handler),
    )

    models = await provider.list_models()

    assert [model.model_id for model in models] == ["m1", "m2"]
    assert models[0].supports_tools is True


@pytest.mark.asyncio
async def test_openrouter_compatible_provider_health_reports_failure_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = OpenRouterCompatibleProvider(
        name="acme",
        base_url="https://acme.example/api/v1",
        api_token="test-token",
        transport=httpx.MockTransport(handler),
    )

    health = await provider.health()

    assert health.ok is False
    assert health.error is not None


def test_parse_extra_providers_skips_malformed_entries():
    seed = json.dumps(
        [
            {"name": "acme", "base_url": "https://acme.example/api/v1", "api_token": "x"},
            {"name": "missing-base-url"},
            "not-a-dict",
        ]
    )
    parsed = _parse_extra_providers(seed)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "acme"


def test_parse_extra_providers_handles_malformed_json():
    assert _parse_extra_providers("") == []
    assert _parse_extra_providers("not json") == []
    assert _parse_extra_providers("{}") == []


def test_discover_providers_includes_openrouter_when_key_configured():
    settings = Settings(openrouter_api_key="test-key")
    providers = discover_providers(settings)
    assert any(provider.name == "openrouter" for provider in providers)


def test_discover_providers_includes_configured_extra_providers():
    settings = Settings(
        openrouter_api_key="",
        provider_framework_extra_providers_json=json.dumps(
            [{"name": "acme", "base_url": "https://acme.example/api/v1", "api_token": "x"}]
        ),
    )
    providers = discover_providers(settings)
    assert [provider.name for provider in providers] == ["acme"]

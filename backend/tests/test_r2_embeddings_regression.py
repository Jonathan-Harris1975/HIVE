from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from app.core.config import Settings
from app.services import embeddings
from app.services.connectors import r2_connector
from app.storage.r2 import R2Storage


def _r2_settings() -> Settings:
    return Settings(
        CF_R2_ENDPOINT_URL="https://example.r2.cloudflarestorage.com",
        CF_R2_ACCESS_KEY_ID="r2-access-key",
        CF_R2_SECRET_ACCESS_KEY="r2-secret-key",
        CF_R2_BUCKET="hive-test",
    )


def _embedding_settings() -> Settings:
    return Settings(
        EMBEDDINGS_ENABLED=True,
        EMBEDDINGS_PROVIDER="cloudflare",
        EMBEDDINGS_ACCOUNT_ID="account-123",
        EMBEDDINGS_API_TOKEN="embedding-secret-token",
        EMBEDDINGS_MODEL="@cf/test/model",
        EMBEDDINGS_TIMEOUT_SECONDS=3,
    )


class _ConnectorStorageStub:
    keys: list[str] = []
    error: Exception | None = None

    def __init__(self, settings: Settings) -> None:
        self.enabled = True
        self.write_enabled = True
        self.read_enabled = True

    def list_keys(self, limit: int = 1000) -> list[str]:
        assert limit == 1
        if self.error is not None:
            raise self.error
        return list(self.keys)


@pytest.mark.asyncio
async def test_r2_connector_success_reports_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    _ConnectorStorageStub.keys = ["uploads/example.txt"]
    _ConnectorStorageStub.error = None
    monkeypatch.setattr(r2_connector, "R2Storage", _ConnectorStorageStub)

    result = await r2_connector.report(_r2_settings())

    assert result.configured is True
    assert result.healthy is True
    assert result.authenticated is True
    assert result.diagnostics["sample_key_count"] == 1
    assert result.capabilities == ("read", "write", "multi_bucket_read")


@pytest.mark.asyncio
async def test_r2_connector_empty_result_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _ConnectorStorageStub.keys = []
    _ConnectorStorageStub.error = None
    monkeypatch.setattr(r2_connector, "R2Storage", _ConnectorStorageStub)

    result = await r2_connector.report(_r2_settings())

    assert result.healthy is True
    assert result.diagnostics["sample_key_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("InvalidAccessKeyId"),
        RuntimeError("AccessDenied"),
        RuntimeError("temporary endpoint failure"),
    ],
)
async def test_r2_connector_degrades_on_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _ConnectorStorageStub.error = error
    monkeypatch.setattr(r2_connector, "R2Storage", _ConnectorStorageStub)

    result = await r2_connector.report(_r2_settings())

    assert result.configured is True
    assert result.healthy is False
    assert result.authenticated is False
    assert result.error


@pytest.mark.asyncio
async def test_r2_connector_redacts_credentials_from_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _r2_settings()
    _ConnectorStorageStub.error = RuntimeError(
        f"request failed for {settings.cf_r2_access_key_id}:{settings.cf_r2_secret_access_key}"
    )
    monkeypatch.setattr(r2_connector, "R2Storage", _ConnectorStorageStub)

    result = await r2_connector.report(settings)

    assert settings.cf_r2_access_key_id not in (result.error or "")
    assert settings.cf_r2_secret_access_key not in (result.error or "")
    assert "[redacted]" in (result.error or "")


class _PagedListClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_objects_v2(self, **request: Any) -> dict[str, Any]:
        self.calls.append(request)
        token = request.get("ContinuationToken")
        if token is None:
            return {
                "Contents": [{"Key": "a.txt", "Size": 3}],
                "CommonPrefixes": [{"Prefix": "folder/"}],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            }
        assert token == "page-2"
        return {
            "Contents": [{"Key": "b.txt", "Size": 5}],
            "CommonPrefixes": [],
            "IsTruncated": False,
        }


def test_r2_storage_pagination_cursor_round_trip() -> None:
    storage = R2Storage(_r2_settings())
    client = _PagedListClient()
    storage._client = client

    first = storage.list_objects_page(limit=1)
    second = storage.list_objects_page(limit=1, cursor=first.next_cursor)

    assert [item.key for item in first.objects] == ["a.txt"]
    assert first.next_cursor == "page-2"
    assert first.truncated is True
    assert first.prefixes == ["folder/"]
    assert [item.key for item in second.objects] == ["b.txt"]
    assert second.next_cursor is None
    assert client.calls[1]["ContinuationToken"] == "page-2"


class _ErrorListClient:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def list_objects_v2(self, **request: Any) -> dict[str, Any]:
        raise self.error


def _client_error(code: str, message: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": "req-123"},
        },
        "ListObjectsV2",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_client_error("InvalidAccessKeyId", "bad credentials", 403), "InvalidAccessKeyId"),
        (_client_error("AccessDenied", "forbidden", 403), "AccessDenied"),
    ],
)
def test_r2_storage_translates_client_errors(error: Exception, expected: str) -> None:
    storage = R2Storage(_r2_settings())
    storage._client = _ErrorListClient(error)

    with pytest.raises(RuntimeError) as raised:
        storage.list_objects_page(limit=1)

    text = str(raised.value)
    assert expected in text
    assert "http_status=403" in text
    assert "r2-secret-key" not in text


def test_r2_storage_translates_transient_network_error() -> None:
    storage = R2Storage(_r2_settings())
    storage._client = _ErrorListClient(
        EndpointConnectionError(endpoint_url="https://example.r2.cloudflarestorage.com")
    )

    with pytest.raises(RuntimeError, match="R2 list failed"):
        storage.list_objects_page(limit=1)


class _MalformedListClient:
    def __init__(self, response: Any) -> None:
        self.response = response

    def list_objects_v2(self, **request: Any) -> Any:
        return self.response


@pytest.mark.parametrize(
    "response",
    [None, [], {"Contents": "not-a-list"}, {"Contents": ["not-a-dict"]}],
)
def test_r2_storage_rejects_malformed_list_responses(response: Any) -> None:
    storage = R2Storage(_r2_settings())
    storage._client = _MalformedListClient(response)

    with pytest.raises(RuntimeError, match="R2 list returned"):
        storage.list_objects_page(limit=1)


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_embeddings_success_extracts_expected_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer embedding-secret-token"
        return httpx.Response(
            200,
            json={"result": {"data": [[0.1, 0.2], [1, 2]]}},
        )

    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(), transport=_transport(handler)
    )
    result = await client.embed_texts(["one", "two"])

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["dimensions"] == 2
    assert result["vectors"] == [[0.1, 0.2], [1.0, 2.0]]


def test_extract_vectors_accepts_common_provider_shapes() -> None:
    assert embeddings._extract_vectors({"result": [1, 2]}) == [[1.0, 2.0]]
    assert embeddings._extract_vectors({"result": {"embeddings": [{"embedding": [3, 4]}]}}) == [
        [3.0, 4.0]
    ]
    assert embeddings._extract_vectors({"vectors": [{"values": [5, 6]}]}) == [[5.0, 6.0]]
    assert embeddings._extract_vectors("bad") == []


@pytest.mark.asyncio
async def test_embeddings_malformed_json_degrades_cleanly() -> None:
    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(),
        transport=_transport(lambda request: httpx.Response(200, text="not-json")),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert result["status_code"] == 200
    assert result["error"] == "Embeddings provider returned malformed JSON."


@pytest.mark.asyncio
async def test_embeddings_valid_json_missing_expected_data() -> None:
    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(),
        transport=_transport(lambda request: httpx.Response(200, json={"result": {"shape": [1, 2]}})),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert "Embedding count mismatch" in str(result["error"])


@pytest.mark.asyncio
async def test_embeddings_non_2xx_uses_provider_error() -> None:
    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(),
        transport=_transport(
            lambda request: httpx.Response(503, json={"errors": [{"message": "temporarily unavailable"}]})
        ),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert result["status_code"] == 503
    assert result["error"] == "temporarily unavailable"


@pytest.mark.asyncio
async def test_embeddings_auth_rejection_is_reported_without_token() -> None:
    settings = _embedding_settings()
    client = embeddings.CloudflareEmbeddingsClient(
        settings,
        transport=_transport(
            lambda request: httpx.Response(
                401,
                json={"errors": [{"message": f"rejected token {settings.embeddings_api_token}"}]},
            )
        ),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert result["status_code"] == 401
    assert settings.embeddings_api_token not in str(result)
    assert "[redacted]" in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("factory", "expected_type"),
    [
        (lambda request: httpx.ReadTimeout("timed out", request=request), "ReadTimeout"),
        (lambda request: httpx.ConnectError("connection failed", request=request), "ConnectError"),
    ],
)
async def test_embeddings_network_failures_degrade(factory, expected_type: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise factory(request)

    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(), transport=_transport(handler)
    )
    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert result["type"] == expected_type


@pytest.mark.asyncio
async def test_embeddings_unexpected_json_type_degrades_cleanly() -> None:
    client = embeddings.CloudflareEmbeddingsClient(
        _embedding_settings(),
        transport=_transport(lambda request: httpx.Response(200, json=[[1, 2, 3]])),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert result["error"] == "Unexpected embeddings response type: list."


@pytest.mark.asyncio
async def test_embeddings_disabled_and_empty_input_are_non_fatal() -> None:
    disabled = embeddings.CloudflareEmbeddingsClient(Settings(EMBEDDINGS_ENABLED=False))
    assert (await disabled.embed_texts(["one"]))["enabled"] is False

    enabled = embeddings.CloudflareEmbeddingsClient(_embedding_settings())
    empty = await enabled.embed_texts([])
    assert empty["ok"] is False
    assert empty["enabled"] is True
    assert "No texts" in str(empty["error"])


@pytest.mark.asyncio
async def test_embeddings_exception_logging_redacts_token(caplog: pytest.LogCaptureFixture) -> None:
    settings = _embedding_settings()

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"provider exploded with {settings.embeddings_api_token}")

    caplog.set_level(logging.WARNING, logger="uvicorn.error.hive.embeddings")
    client = embeddings.CloudflareEmbeddingsClient(settings, transport=_transport(handler))
    result = await client.embed_texts(["one"])

    assert settings.embeddings_api_token not in str(result)
    assert settings.embeddings_api_token not in caplog.text
    assert "[redacted]" in str(result)
    assert "[redacted]" in caplog.text


@pytest.mark.asyncio
async def test_embeddings_malformed_error_body_is_secret_safe() -> None:
    settings = _embedding_settings()
    client = embeddings.CloudflareEmbeddingsClient(
        settings,
        transport=_transport(
            lambda request: httpx.Response(
                500,
                text=f"upstream echoed {settings.embeddings_api_token}",
            )
        ),
    )

    result = await client.embed_texts(["one"])

    assert result["ok"] is False
    assert settings.embeddings_api_token not in str(result)
    assert "[redacted]" in str(result)

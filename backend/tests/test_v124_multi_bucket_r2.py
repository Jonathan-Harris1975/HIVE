from __future__ import annotations

import io
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.storage.r2 import ObjectListPage, ObjectMetadata, ObjectStream, ObjectSummary, R2Storage, ReadObject


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "test",
        "cf_r2_account_id": "account",
        "cf_r2_access_key_id": "write-key",
        "cf_r2_secret_access_key": "write-secret",
        "cf_r2_bucket": "hive",
        "r2_multi_bucket_read_enabled": True,
        "r2_read_access_key_id": "read-key",
        "r2_read_secret_access_key": "read-secret",
        "r2_bucket_audits": "audits",
        "r2_public_base_url_audits": "https://audits.example.test",
    }
    values.update(overrides)
    return Settings(**values)


def test_lane_registry_exposes_read_only_access() -> None:
    client = TestClient(create_app(_settings()))

    body = client.get("/v1/files/r2-lanes").json()

    lanes = {item["lane"]: item for item in body["lanes"]}
    assert body["multi_bucket_read_enabled"] is True
    assert lanes["uploads"]["access_mode"] == "read_write"
    assert lanes["uploads"]["writable"] is True
    assert lanes["audits"]["access_mode"] == "read_only"
    assert lanes["audits"]["readable"] is True
    assert lanes["audits"]["writable"] is False


def test_list_read_metadata_and_download_use_scoped_read_credentials(monkeypatch) -> None:
    calls: list[tuple[str, bool, str]] = []

    def fake_list(self, **kwargs):  # noqa: ANN001
        calls.append(("list", bool(kwargs["read_only"]), str(kwargs["bucket"])))
        return ObjectListPage(
            objects=[
                ObjectSummary(
                    key="reports/latest.md",
                    size_bytes=12,
                    last_modified=datetime.now(UTC).isoformat(),
                    public_url="https://audits.example.test/reports/latest.md",
                )
            ],
            prefixes=["reports/archive/"],
            next_cursor=None,
            scanned_count=1,
            truncated=False,
        )

    def fake_head(self, key, **kwargs):  # noqa: ANN001
        calls.append(("head", bool(kwargs["read_only"]), str(kwargs["bucket"])))
        return ObjectMetadata(
            key=key,
            bucket=str(kwargs["bucket"]),
            size_bytes=12,
            content_type="text/markdown",
            public_url="https://audits.example.test/reports/latest.md",
        )

    def fake_read(self, key, max_bytes, **kwargs):  # noqa: ANN001, ARG001
        calls.append(("read", bool(kwargs["read_only"]), str(kwargs["bucket"])))
        return ReadObject(
            key=key,
            bucket=str(kwargs["bucket"]),
            content=b"audit result",
            size_bytes=12,
            content_type="text/markdown",
            public_url="https://audits.example.test/reports/latest.md",
        )

    def fake_open(self, key, **kwargs):  # noqa: ANN001
        calls.append(("download", bool(kwargs["read_only"]), str(kwargs["bucket"])))
        return ObjectStream(
            key=key,
            bucket=str(kwargs["bucket"]),
            body=io.BytesIO(b"audit result"),
            size_bytes=12,
            content_type="text/markdown",
        )

    monkeypatch.setattr(R2Storage, "list_objects_page", fake_list)
    monkeypatch.setattr(R2Storage, "head_object", fake_head)
    monkeypatch.setattr(R2Storage, "read_object", fake_read)
    monkeypatch.setattr(R2Storage, "open_object", fake_open)
    client = TestClient(create_app(_settings()))

    listed = client.get("/v1/files/r2/audits/objects", params={"prefix": "reports/"})
    metadata = client.get(
        "/v1/files/r2/audits/metadata",
        params={"key": "reports/latest.md"},
    )
    read = client.get(
        "/v1/files/r2/audits/read",
        params={"key": "reports/latest.md"},
    )
    download = client.get(
        "/v1/files/r2/audits/download",
        params={"key": "reports/latest.md"},
    )

    assert listed.status_code == 200
    assert listed.json()["prefixes"] == ["reports/archive/"]
    assert metadata.json()["preview_supported"] is True
    assert read.json()["content"] == "audit result"
    assert read.json()["file"]["lane"] == "audits"
    assert download.content == b"audit result"
    assert download.headers["x-hive-r2-lane"] == "audits"
    assert calls == [
        ("list", True, "audits"),
        ("head", True, "audits"),
        ("read", True, "audits"),
        ("download", True, "audits"),
    ]


def test_non_upload_lane_chat_uses_bounded_direct_read(monkeypatch) -> None:
    def fake_read(self, key, max_bytes, **kwargs):  # noqa: ANN001, ARG001
        assert kwargs["read_only"] is True
        assert kwargs["bucket"] == "audits"
        return ReadObject(
            key=key,
            bucket="audits",
            content=b"The audit says future RSS wording needs a clearer source label.",
            size_bytes=64,
            content_type="text/plain",
            public_url="https://audits.example.test/reports/latest.txt",
        )

    monkeypatch.setattr(R2Storage, "read_object", fake_read)
    client = TestClient(create_app(_settings()))

    response = client.post(
        "/v1/chat/with-file",
        json={
            "lane": "audits",
            "object_key": "reports/latest.txt",
            "message": "What is the recommendation?",
            "dry_run": True,
            "use_chunks": True,
            "auto_chunk": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"]["lane"] == "audits"
    assert body["source_citation"]["lane"] == "audits"
    assert body["source_chunks"] == []
    assert "bounded direct read" in body["chunk_mode_note"]


def test_multi_bucket_enabled_requires_read_credentials_in_production() -> None:
    from app.core.production import build_readiness_report

    settings = _settings(
        app_env="production",
        admin_bearer_token="x" * 40,
        cors_origins=["https://hive-ui.pages.dev"],
        openrouter_api_key="test",
        r2_read_access_key_id="",
        r2_read_secret_access_key="",
    )

    report = build_readiness_report(settings)

    check = next(item for item in report.checks if item.name == "r2_multi_bucket_read")
    assert check.status == "error"

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


def test_lane_registry_exposes_read_write_access() -> None:
    client = TestClient(create_app(_settings()))

    body = client.get("/v1/files/r2-lanes").json()

    lanes = {item["lane"]: item for item in body["lanes"]}
    assert body["multi_bucket_read_enabled"] is True
    assert lanes["uploads"]["access_mode"] == "read_write"
    assert lanes["uploads"]["writable"] is True
    assert lanes["audits"]["access_mode"] == "read_write"
    assert lanes["audits"]["readable"] is True
    assert lanes["audits"]["writable"] is True


def test_list_read_metadata_download_and_view_use_write_credentials_when_writable(monkeypatch) -> None:
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
    view = client.get(
        "/v1/files/r2/audits/view",
        params={"key": "reports/latest.md"},
    )

    assert listed.status_code == 200
    assert listed.json()["prefixes"] == ["reports/archive/"]
    assert metadata.json()["preview_supported"] is True
    assert read.json()["content"] == "audit result"
    assert read.json()["file"]["lane"] == "audits"
    assert download.content == b"audit result"
    assert download.headers["x-hive-r2-lane"] == "audits"
    assert view.content == b"audit result"
    assert view.headers["content-disposition"].startswith("inline")
    assert calls == [
        ("list", False, "audits"),
        ("head", False, "audits"),
        ("read", False, "audits"),
        ("download", False, "audits"),
        ("download", False, "audits"),
    ]


def test_non_upload_lane_chat_uses_bounded_direct_read(monkeypatch) -> None:
    def fake_read(self, key, max_bytes, **kwargs):  # noqa: ANN001, ARG001
        assert kwargs["read_only"] is False
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


def test_multi_bucket_enabled_accepts_shared_write_credentials_in_production() -> None:
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
    assert check.status == "ok"


def test_r2_client_trims_koyeb_secret_whitespace(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(service: str, **kwargs):  # noqa: ANN001
        captured["service"] = service
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.storage.r2.boto3.client", fake_client)
    settings = _settings(
        cf_r2_endpoint_url="  https://account.r2.cloudflarestorage.com/  ",
        r2_read_access_key_id="  read-key  ",
        r2_read_secret_access_key="  read-secret\n",
        r2_region=" auto ",
        r2_addressing_style=" path ",
    )

    R2Storage(settings).client(read_only=True)

    assert captured["service"] == "s3"
    assert captured["endpoint_url"] == "https://account.r2.cloudflarestorage.com"
    assert captured["aws_access_key_id"] == "read-key"
    assert captured["aws_secret_access_key"] == "read-secret"
    assert captured["region_name"] == "auto"


def test_multi_bucket_file_chat_accepts_multiple_selected_r2_objects(monkeypatch) -> None:
    calls: list[tuple[str, str, bool]] = []

    def fake_read(self, key, max_bytes, **kwargs):  # noqa: ANN001, ARG001
        bucket = str(kwargs["bucket"])
        calls.append((key, bucket, bool(kwargs["read_only"])))
        content = {
            ("uploads/brief.txt", "hive"): b"Upload brief says inspect deployment adapters.",
            ("reports/audit.txt", "audits"): b"Audit report says verify R2 lane access.",
        }[(key, bucket)]
        return ReadObject(
            key=key,
            bucket=bucket,
            content=content,
            size_bytes=len(content),
            content_type="text/plain",
            public_url=f"https://{bucket}.example.test/{key}",
        )

    monkeypatch.setattr(R2Storage, "read_object", fake_read)
    client = TestClient(create_app(_settings()))

    response = client.post(
        "/v1/chat/with-file",
        json={
            "message": "Compare the selected files.",
            "dry_run": True,
            "use_chunks": True,
            "files": [
                {"lane": "uploads", "object_key": "uploads/brief.txt", "name": "Brief"},
                {"lane": "audits", "object_key": "reports/audit.txt", "name": "Audit"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["source_label"] if "source_label" in body else True
    assert [item["lane"] for item in body["source_citations"]] == ["uploads", "audits"]
    assert [item["label"] for item in body["source_citations"]] == ["Brief", "Audit"]
    assert "selected R2 files used bounded direct reads" in body["chunk_mode_note"]
    assert calls == [("uploads/brief.txt", "hive", False), ("reports/audit.txt", "audits", False)]


def test_delete_r2_lane_objects_is_lane_scoped_and_write_gated(monkeypatch) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_delete(self, keys, **kwargs):  # noqa: ANN001
        calls.append((list(keys), str(kwargs["bucket"])))
        return {
            "ok": True,
            "bucket": str(kwargs["bucket"]),
            "requested_count": len(keys),
            "deleted_count": len(keys),
            "deleted_keys": list(keys),
            "errors": [],
        }

    monkeypatch.setattr(R2Storage, "delete_objects", fake_delete)
    client = TestClient(create_app(_settings()))

    response = client.request(
        "DELETE",
        "/v1/files/r2/audits/objects",
        json={"object_keys": ["reports/a.txt", "reports/b.txt", "reports/a.txt"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["lane"] == "audits"
    assert body["deleted_count"] == 2
    assert body["deleted_keys"] == ["reports/a.txt", "reports/b.txt"]
    assert calls == [(["reports/a.txt", "reports/b.txt"], "audits")]


def test_delete_r2_lane_objects_refuses_non_writable_lanes(monkeypatch) -> None:
    called = False

    def fake_delete(self, keys, **kwargs):  # noqa: ANN001, ARG001
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(R2Storage, "delete_objects", fake_delete)
    client = TestClient(create_app(_settings(r2_multi_bucket_write_enabled=False)))

    response = client.request(
        "DELETE",
        "/v1/files/r2/audits/objects",
        json={"object_key": "reports/a.txt"},
    )

    assert response.status_code == 503
    assert called is False


def test_recursive_lane_list_flattens_selected_prefix(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_list(self, **kwargs):  # noqa: ANN001, ARG001
        captured.update(kwargs)
        return ObjectListPage(
            objects=[
                ObjectSummary(
                    key="reports/2026/june/audit.md",
                    size_bytes=17,
                    last_modified=datetime.now(UTC).isoformat(),
                    public_url="https://audits.example.test/reports/2026/june/audit.md",
                )
            ],
            prefixes=[],
            next_cursor=None,
            scanned_count=1,
            truncated=False,
        )

    monkeypatch.setattr(R2Storage, "list_objects_page", fake_list)
    client = TestClient(create_app(_settings()))

    response = client.get(
        "/v1/files/r2/audits/objects",
        params={"prefix": "reports/", "limit": "8", "recursive": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recursive"] is True
    assert body["files"][0]["key"] == "reports/2026/june/audit.md"
    assert body["prefixes"] == []
    assert captured["prefix"] == "reports/"
    assert captured["delimiter"] is None
    assert captured["limit"] == 8

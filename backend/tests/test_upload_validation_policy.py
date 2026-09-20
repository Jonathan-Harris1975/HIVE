from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.main import app


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_base64_rejects_x_msdownload_without_persistence(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "payload.bin",
            "content_type": "application/x-msdownload",
            "content_base64": _b64(b"not-an-executable"),
        },
    )

    assert response.status_code == 415
    listed = client.get("/v1/files/list").json()
    assert listed["count"] == 0


def test_base64_rejects_exe_with_octet_stream(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "payload.exe",
            "content_type": "application/octet-stream",
            "content_base64": _b64(b"MZfake"),
        },
    )

    assert response.status_code == 415
    assert client.get("/v1/files/list").json()["count"] == 0


def test_base64_rejects_extension_mime_mismatch(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "report.pdf",
            "content_type": "text/plain",
            "content_base64": _b64(b"plain text pretending to be a PDF"),
        },
    )

    assert response.status_code == 415
    assert client.get("/v1/files/list").json()["count"] == 0


def test_base64_rejects_executable_signature_with_misleading_mime(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "notes.txt",
            "content_type": "text/plain",
            "content_base64": _b64(b"MZ" + b"\x00" * 32),
        },
    )

    assert response.status_code == 415
    assert client.get("/v1/files/list").json()["count"] == 0


def test_base64_rejects_data_url_supplied_executable_mime(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "payload.bin",
            "content_base64": "data:application/x-msdownload;base64," + _b64(b"MZfake"),
        },
    )

    assert response.status_code == 415
    assert client.get("/v1/files/list").json()["count"] == 0


def test_known_safe_base64_upload_succeeds(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/v1/files/upload-base64",
        json={
            "filename": "safe.txt",
            "content_type": "text/plain",
            "content_base64": _b64(b"safe repository evidence"),
        },
    )

    assert response.status_code == 200
    assert response.json()["file"]["original_name"] == "safe.txt"


def test_multipart_and_text_routes_share_canonical_policy(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    client = TestClient(app)

    multipart = client.post(
        "/v1/files/upload",
        files={"upload": ("payload.exe", b"MZfake", "application/octet-stream")},
    )
    text = client.post(
        "/v1/files/upload-text",
        json={"filename": "payload.exe", "content": "echo nope", "content_type": "text/plain"},
    )

    assert multipart.status_code == 415
    assert text.status_code == 415
    assert client.get("/v1/files/list").json()["count"] == 0

from __future__ import annotations

import base64
import binascii
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.file_ingestion import ingest_bytes_content, ingest_text_content, ingest_upload
from app.ingestion.zip_ingestion import UnsafeZipError, inspect_zip
from app.services.brand_modes import build_system_prompt
from app.services.context_manager import ContextWindow
from app.services.model_router import Mode, ModelRouter
from app.services.openrouter import OpenRouterClient
from app.storage.local_blob import LocalBlobStorage
from app.storage.r2 import R2Storage

router = APIRouter(tags=["files"], dependencies=[Depends(require_admin)])


class TextUploadRequest(BaseModel):
    filename: str = Field("hive-r2-smoke.txt", min_length=1, max_length=255)
    content: str = Field(..., min_length=0)
    content_type: str | None = "text/plain; charset=utf-8"


class Base64UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., min_length=1)
    content_type: str | None = None


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatWithFileRequest(BaseModel):
    object_key: str = Field(..., min_length=1, max_length=2048)
    message: str = Field(..., min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)
    mode: Mode = Mode.FILE_ANALYSIS
    model: str | None = None
    temperature: float = 0.3
    max_tokens: int = 1200
    max_file_chars: int | None = None


@router.post("/files/upload")
async def upload_file(
    upload: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        result = await ingest_upload(upload, settings)
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return {"ok": True, "file": result.__dict__}


@router.post("/files/upload-text")
async def upload_text(
    payload: TextUploadRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        result = ingest_text_content(
            filename=payload.filename,
            content=payload.content,
            content_type=payload.content_type or "text/plain; charset=utf-8",
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return {"ok": True, "file": result.__dict__}


@router.post("/files/upload-base64")
async def upload_base64(
    payload: Base64UploadRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Upload arbitrary bytes from JSON.

    Useful for ReqBin/Make.com/phone tests where multipart file upload is awkward.
    The payload may contain plain base64 or a data URL such as
    data:application/zip;base64,....
    """

    try:
        content_type, data = _decode_base64_upload(payload.content_base64, payload.content_type)
        result = ingest_bytes_content(
            filename=payload.filename,
            data=data,
            content_type=content_type,
            settings=settings,
        )
    except binascii.Error as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid base64 payload") from exc
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return {"ok": True, "file": result.__dict__}


@router.get("/files/list")
def list_files(
    prefix: str = Query("uploads/", min_length=0, max_length=512),
    limit: int = Query(50, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List stored files from R2, or local storage in development/test mode."""

    clean_prefix = _normalise_prefix(prefix)
    storage = _storage(settings)
    try:
        objects = storage.list_objects(prefix=clean_prefix, limit=limit)
    except RuntimeError as exc:
        return _storage_error_response(
            operation="list",
            settings=settings,
            key_or_prefix=clean_prefix,
            error=exc,
        )
    return {
        "ok": True,
        "storage": _storage_name(settings),
        "prefix": clean_prefix,
        "count": len(objects),
        "files": [asdict(item) for item in objects],
    }


@router.get("/files/public-url")
def public_url(
    key: str = Query(..., min_length=1, max_length=2048),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return the configured public R2 URL for a key without reading the object."""

    clean_key = _validate_object_key(key)
    storage_name = _storage_name(settings)
    url = R2Storage(settings).public_url_for_key(clean_key) if storage_name == "r2" else None
    return {
        "ok": True,
        "storage": storage_name,
        "object_key": clean_key,
        "public_url": url,
        "public_base_url_configured": bool(settings.cf_r2_public_base_url),
    }


@router.get("/files/read")
def read_file(
    key: str = Query(..., min_length=1, max_length=2048),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Read a small text-ish stored file back from R2/local storage."""

    clean_key = _validate_object_key(key)
    try:
        obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    content, had_decode_replacements = _decode_text(obj.content)
    return {
        "ok": True,
        "file": {
            "object_key": obj.key,
            "storage": _storage_name(settings),
            "bucket": obj.bucket,
            "size_bytes": obj.size_bytes,
            "content_type": obj.content_type,
            "public_url": obj.public_url,
            "decode_replacements": had_decode_replacements,
        },
        "content": content,
    }


@router.get("/files/zip/inspect")
def inspect_stored_zip(
    key: str = Query(..., min_length=1, max_length=2048),
    max_members: int = Query(200, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Inspect a stored ZIP archive without extracting it into the bucket."""

    clean_key = _validate_object_key(key)
    try:
        obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_upload_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="zip_inspect_read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        temp_path = Path(tmp.name)
        tmp.write(obj.content)

    try:
        members = inspect_zip(
            temp_path,
            max_files=settings.max_zip_files,
            max_uncompressed_bytes=settings.max_zip_uncompressed_bytes,
        )
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Stored object is not a valid ZIP archive") from exc
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    files = [member for member in members if not member.is_dir]
    dirs = [member for member in members if member.is_dir]
    return {
        "ok": True,
        "source": _source_metadata(obj, settings, truncated=False, decode_replacements=False),
        "zip": {
            "member_count": len(members),
            "file_count": len(files),
            "directory_count": len(dirs),
            "total_uncompressed_bytes": sum(member.size for member in members),
            "total_compressed_bytes": sum(member.compressed_size for member in members),
            "preview_limit": max_members,
            "members_preview": [asdict(member) for member in members[:max_members]],
        },
    }


@router.post("/chat/with-file")
async def chat_with_file(
    request: ChatWithFileRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Ask a question about one stored R2/local object.

    V1 intentionally injects a bounded text excerpt directly into the model context.
    Vector search/chunk retrieval can replace this later without changing the public route.
    """

    clean_key = _validate_object_key(request.object_key)
    try:
        obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="chat_with_file_read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    content, had_decode_replacements = _decode_text(obj.content)
    max_chars = request.max_file_chars or settings.max_file_chat_chars
    excerpt = content[:max_chars]
    truncated = len(content) > len(excerpt)

    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    effective_mode = router_service.resolve_mode(task, request.mode)
    selected_model = router_service.select_model(task, request.model)
    fallback_models = router_service.fallback_models_for_task(task, selected_model)

    source_label = Path(clean_key).name or clean_key
    window = ContextWindow()
    window.add("system", build_system_prompt(effective_mode))
    window.add(
        "system",
        "Answer using the attached file content only where relevant. "
        "Do not print the object key or public URL inside the answer; "
        "the API returns source metadata separately.",
    )
    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add(
        "user",
        "\n".join(
            [
                request.message,
                "",
                f"Attached file label: {source_label}",
                f"Content type: {obj.content_type or 'unknown'}",
                f"Size bytes: {obj.size_bytes}",
                "",
                "File content excerpt:",
                excerpt,
                "\n[FILE CONTENT TRUNCATED]" if truncated else "",
            ]
        ).strip(),
    )

    payload: dict[str, object] = {
        "model": selected_model,
        "messages": window.trimmed_messages(),
        "temperature": request.temperature,
        "max_tokens": max(request.max_tokens, settings.openrouter_min_response_tokens),
    }
    client = OpenRouterClient(settings)
    completion = await client.chat_completion(payload, fallback_models=fallback_models)
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reply = _reply_text(message.get("content"))
    finish_reason = choice.get("finish_reason")
    empty_reply = not reply.strip()
    ok = not bool(completion.get("_all_attempts_failed")) and not empty_reply
    return {
        "ok": ok,
        "reply": reply,
        "model_used": completion.get("model") or payload.get("model"),
        "provider": completion.get("provider"),
        "usage": completion.get("usage"),
        "raw_finish_reason": finish_reason,
        "completion_truncated": finish_reason == "length",
        "empty_reply": empty_reply,
        "error_code": completion.get("hive_error_code"),
        "attempts": completion.get("hive_attempts"),
        "source": _source_metadata(obj, settings, truncated=truncated, decode_replacements=had_decode_replacements),
        "source_citation": {
            "label": source_label,
            "object_key": obj.key,
            "public_url": obj.public_url,
        },
    }


@router.get("/files/diagnostics")
def files_diagnostics(
    prefix: str = Query("uploads/", min_length=0, max_length=512),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return safe storage diagnostics without exposing secrets."""

    clean_prefix = _normalise_prefix(prefix)
    r2 = R2Storage(settings)
    diagnostics: dict[str, object] = {
        "ok": True,
        "storage": "r2" if r2.enabled else "local",
        "r2_configured": r2.enabled,
        "r2": {
            "bucket": settings.cf_r2_bucket if r2.enabled else None,
            "endpoint_url": settings.r2_endpoint_url if r2.enabled else None,
            "public_base_url_configured": bool(settings.cf_r2_public_base_url),
            "region": settings.r2_region,
            "addressing_style": settings.r2_addressing_style,
            "timeouts": {
                "connect_seconds": settings.r2_connect_timeout_seconds,
                "read_seconds": settings.r2_read_timeout_seconds,
                "max_attempts": settings.r2_max_attempts,
            },
        },
        "prefix": clean_prefix,
        "list_probe": None,
    }

    try:
        objects = _storage(settings).list_objects(prefix=clean_prefix, limit=5)
        diagnostics["list_probe"] = {
            "ok": True,
            "count": len(objects),
            "files": [asdict(item) for item in objects],
        }
    except RuntimeError as exc:
        diagnostics["ok"] = False
        diagnostics["list_probe"] = {
            "ok": False,
            "error": str(exc),
            "hint": _storage_error_hint(str(exc)),
        }
    return diagnostics


def _storage(settings: Settings):
    r2 = R2Storage(settings)
    if r2.enabled:
        return r2
    return LocalBlobStorage()


def _normalise_prefix(prefix: str) -> str:
    if not prefix:
        return ""
    return _validate_object_key(prefix, allow_trailing_slash=True)


def _validate_object_key(key: str, *, allow_trailing_slash: bool = False) -> str:
    clean_key = key.strip().lstrip("/")
    if not clean_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Object key is required")
    parts = [part for part in clean_key.split("/") if part]
    if any(part in {".", ".."} for part in parts) or "\x00" in clean_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid object key")
    if not allow_trailing_slash and clean_key.endswith("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Object key must point to a file")
    return clean_key


def _reply_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _decode_base64_upload(content_base64: str, content_type: str | None) -> tuple[str | None, bytes]:
    raw = content_base64.strip()
    detected_content_type = content_type
    if raw.startswith("data:") and "," in raw:
        header, raw = raw.split(",", 1)
        if ";base64" in header and not detected_content_type:
            detected_content_type = header.removeprefix("data:").split(";", 1)[0] or None
    return detected_content_type, base64.b64decode(raw, validate=True)


def _decode_text(content: bytes) -> tuple[str, bool]:
    decoded = content.decode("utf-8", errors="replace")
    return decoded, "\ufffd" in decoded


def _source_metadata(obj, settings: Settings, *, truncated: bool, decode_replacements: bool) -> dict[str, object]:
    return {
        "object_key": obj.key,
        "storage": _storage_name(settings),
        "bucket": obj.bucket,
        "size_bytes": obj.size_bytes,
        "content_type": obj.content_type,
        "public_url": obj.public_url,
        "truncated": truncated,
        "decode_replacements": decode_replacements,
    }


def _storage_name(settings: Settings) -> str:
    return "r2" if R2Storage(settings).enabled else "local"


def _storage_error_response(
    *,
    operation: str,
    settings: Settings,
    key_or_prefix: str,
    error: RuntimeError,
) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "operation": operation,
            "message": str(error),
            "hint": _storage_error_hint(str(error)),
        },
        "storage": _storage_name(settings),
        "key_or_prefix": key_or_prefix,
        "r2_configured": R2Storage(settings).enabled,
        "r2": {
            "bucket": settings.cf_r2_bucket if R2Storage(settings).enabled else None,
            "endpoint_url": settings.r2_endpoint_url if R2Storage(settings).enabled else None,
            "public_base_url_configured": bool(settings.cf_r2_public_base_url),
            "addressing_style": settings.r2_addressing_style,
        },
    }


def _storage_error_hint(message: str) -> str:
    lowered = message.lower()
    if "accessdenied" in lowered or "forbidden" in lowered or "403" in lowered:
        return "R2 credentials are valid enough to reach R2, but this operation is not allowed. Check bucket permissions for list/read/write."
    if "nosuchbucket" in lowered or ("not found" in lowered and "bucket" in lowered):
        return "R2 bucket was not found. Check R2_BUCKET_UPLOADS/R2_BUCKET and account endpoint."
    if "nosuchkey" in lowered or "404" in lowered:
        return "Object key was not found. Use /v1/files/list to copy the exact key."
    if "timed out" in lowered or "timeout" in lowered:
        return "R2 operation timed out. Retry, then increase R2_READ_TIMEOUT_SECONDS if needed."
    if "signature" in lowered or "invalidaccesskeyid" in lowered:
        return "R2 signature/auth failed. Check R2 access key, secret key, endpoint and region."
    return "Check the R2 bucket name, endpoint URL, key permissions and exact object key."

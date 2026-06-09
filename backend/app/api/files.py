from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.file_ingestion import ingest_text_content, ingest_upload
from app.ingestion.zip_ingestion import UnsafeZipError
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

    window = ContextWindow()
    window.add("system", build_system_prompt(effective_mode))
    window.add("system", f"Answer using the attached file content. Cite this object key when relevant: {clean_key}")
    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add(
        "user",
        "\n".join(
            [
                request.message,
                "",
                f"Source object key: {clean_key}",
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
        "max_tokens": request.max_tokens,
    }
    client = OpenRouterClient(settings)
    completion = await client.chat_completion(payload, fallback_models=fallback_models)
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    ok = not bool(completion.get("_all_attempts_failed"))
    return {
        "ok": ok,
        "reply": message.get("content", ""),
        "model_used": completion.get("model") or payload.get("model"),
        "provider": completion.get("provider"),
        "usage": completion.get("usage"),
        "raw_finish_reason": choice.get("finish_reason"),
        "attempts": completion.get("hive_attempts"),
        "source": {
            "object_key": obj.key,
            "storage": _storage_name(settings),
            "bucket": obj.bucket,
            "size_bytes": obj.size_bytes,
            "content_type": obj.content_type,
            "public_url": obj.public_url,
            "truncated": truncated,
            "decode_replacements": had_decode_replacements,
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


def _decode_text(content: bytes) -> tuple[str, bool]:
    decoded = content.decode("utf-8", errors="replace")
    return decoded, "\ufffd" in decoded


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

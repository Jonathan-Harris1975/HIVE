from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
import time
import zipfile
from dataclasses import asdict
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.chunking import chunks_to_dicts, split_text_into_chunks
from app.ingestion.file_ingestion import ingest_bytes_content, ingest_text_content, ingest_upload
from app.ingestion.text_extractors import extract_text_with_metadata
from app.ingestion.zip_ingestion import UnsafeZipError, extract_text_from_zip, inspect_zip
from app.services.brand_modes import build_system_prompt
from app.services.context_manager import ContextWindow
from app.services.model_router import Mode, ModelRouter
from app.services.openrouter import OpenRouterClient
from app.services.skill_registry import get_skill_catalogue_item
from app.services.workflow_presets import (
    allowed_workflow_presets,
    apply_workflow_preset_to_request,
    get_workflow_preset,
)
from app.services.embeddings import CloudflareEmbeddingsClient
from app.storage.local_blob import LocalBlobStorage
from app.storage.r2 import R2Storage
from app.storage.sql_store import SqlStore
from app.storage.vectorize import VectorizeClient

router = APIRouter(tags=["files"], dependencies=[Depends(require_admin)])


class TextUploadRequest(BaseModel):
    filename: str = Field("hive-r2-smoke.txt", min_length=1, max_length=255)
    content: str = Field(..., min_length=0)
    content_type: str | None = "text/plain; charset=utf-8"
    lane: str = Field("uploads", min_length=1, max_length=80)
    test_run_id: str | None = Field(None, max_length=120)


class Base64UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., min_length=1)
    content_type: str | None = None
    lane: str = Field("uploads", min_length=1, max_length=80)
    test_run_id: str | None = Field(None, max_length=120)


class FileChunkRequest(BaseModel):
    object_key: str = Field(..., min_length=1, max_length=2048)
    max_chars: int | None = Field(None, ge=80, le=24000)
    overlap_chars: int | None = Field(None, ge=0, le=12000)
    max_chunks: int | None = Field(None, ge=1, le=5000)
    replace_existing: bool = True
    test_run_id: str | None = Field(None, max_length=120)


class FileVectorizeRequest(BaseModel):
    object_key: str = Field(..., min_length=1, max_length=2048)
    auto_chunk: bool = True
    replace_existing_chunks: bool = False
    chunk_limit: int | None = Field(None, ge=1, le=5000)
    batch_size: int | None = Field(None, ge=1, le=100)
    test_run_id: str | None = Field(None, max_length=120)


class ZipExtractTextRequest(BaseModel):
    object_key: str = Field(..., min_length=1, max_length=2048)
    recursive: bool = True
    chunk: bool = True
    vectorize: bool = False
    replace_existing_chunks: bool = True
    max_members: int | None = Field(None, ge=1, le=1000)
    max_member_bytes: int | None = Field(None, ge=1024, le=25 * 1024 * 1024)
    max_total_text_chars: int | None = Field(None, ge=1000, le=1_000_000)
    max_depth: int | None = Field(None, ge=0, le=5)
    test_run_id: str | None = Field(None, max_length=120)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class FileReference(BaseModel):
    object_key: str = Field(..., min_length=1, max_length=2048)
    lane: str = Field("uploads", min_length=1, max_length=80)
    name: str | None = Field(None, max_length=255)


class DeleteR2ObjectsRequest(BaseModel):
    object_key: str | None = Field(None, min_length=1, max_length=2048)
    object_keys: list[str] = Field(default_factory=list, max_length=1000)
    dry_run: bool = False


class ChatWithFileRequest(BaseModel):
    object_key: str | None = Field(None, min_length=1, max_length=2048)
    lane: str = Field("uploads", min_length=1, max_length=80)
    files: list[FileReference] = Field(default_factory=list, max_length=20)
    message: str = Field(..., min_length=1)
    history: list[ChatTurn] = Field(default_factory=list)
    mode: Mode = Mode.FILE_ANALYSIS
    model: str | None = None
    temperature: float = 0.3
    max_tokens: int = 1200
    max_file_chars: int | None = None
    model_timeout_seconds: float | None = Field(
        None,
        ge=0.5,
        le=120,
        description="Optional timeout just for the model-call stage.",
    )
    skip_model: bool = Field(
        False,
        description="If true, read/build context only and return diagnostics without calling OpenRouter.",
    )
    dry_run: bool = Field(
        False,
        description="Alias for skip_model, useful for smoke tests and diagnosing file-context build issues.",
    )
    conversation_id: str | None = None
    use_chunks: bool = Field(
        False, description="Use persisted SQL chunks instead of injecting the raw file excerpt."
    )
    use_vectorize: bool = Field(
        False, description="When use_chunks is true, try Vectorize semantic retrieval first."
    )
    vectorize_fallback_sql: bool = Field(
        True,
        description="Fall back to SQL lexical chunk search if Vectorize is disabled, empty, or fails.",
    )
    chunk_query: str | None = Field(
        None, max_length=2000, description="Optional retrieval query; defaults to the user message."
    )
    chunk_limit: int | None = Field(None, ge=1, le=30)
    auto_chunk: bool = Field(
        False,
        description="If use_chunks is true and no chunks exist, read the file and create chunks first.",
    )
    test_run_id: str | None = Field(None, max_length=120)
    workflow_preset: str | None = Field(
        None,
        max_length=80,
        description="Optional v1.6 preset such as audit_report_review, repo_debug_bundle, ci_log_analysis, social_content_qa, podcast_episode_review, or ebook_keyword_review.",
    )
    skill_id: str | None = Field(
        None,
        max_length=120,
        description="Optional existing HIVE skill id to apply as guidance while working with this file.",
    )
    skill_title: str | None = Field(
        None,
        max_length=180,
        description="UI display title for the selected skill, used as a fallback when the registry lookup is unavailable.",
    )


@router.post("/files/upload")
async def upload_file(
    upload: UploadFile = File(...),
    lane: str = Query("uploads", min_length=1, max_length=80),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    lane_config = _target_upload_lane(settings, lane)
    try:
        result = await ingest_upload(upload, settings, lane_config=lane_config)
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    db_record = SqlStore(settings).record_file(
        result, extra_metadata=_upload_metadata(lane_config)
    )
    return {
        "ok": True,
        "file": result.__dict__,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
    }


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
            lane_config=_target_upload_lane(settings, payload.lane),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    lane_config = _target_upload_lane(settings, payload.lane)
    db_record = SqlStore(settings).record_file(
        result, extra_metadata=_upload_metadata(lane_config, payload.test_run_id)
    )
    return {
        "ok": True,
        "file": result.__dict__,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
    }


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
            lane_config=_target_upload_lane(settings, payload.lane),
        )
    except binascii.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid base64 payload"
        ) from exc
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    lane_config = _target_upload_lane(settings, payload.lane)
    db_record = SqlStore(settings).record_file(
        result, extra_metadata=_upload_metadata(lane_config, payload.test_run_id)
    )
    return {
        "ok": True,
        "file": result.__dict__,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
    }


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


@router.get("/files/r2-lanes")
def r2_lanes(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return configured R2 ecosystem lanes without exposing credentials.

    HIVE uses this as a registry so it can reason about AIMS/RAMS/website/
    podcast artefact locations. Configured lanes are read/write when the shared
    server-side R2 write credentials allow it.
    """

    lanes = settings.r2_ecosystem_lanes
    return {
        "ok": True,
        "count": len(lanes),
        "configured_count": sum(1 for item in lanes if item.get("configured")),
        "primary_upload_lane": "uploads",
        "lanes": lanes,
        "multi_bucket_read_enabled": settings.r2_multi_bucket_read_enabled,
        "multi_bucket_write_enabled": settings.r2_multi_bucket_write_enabled,
        "read_credentials_configured": settings.r2_read_credentials_configured,
        "write_credentials_configured": settings.r2_write_credentials_configured,
        "note": "Every configured bucket can be read/write when shared R2 write credentials are configured.",
    }


@router.get("/files/r2-lanes/public-url")
def r2_lane_public_url(
    lane: str = Query(..., min_length=1, max_length=80),
    key: str = Query("", max_length=2048),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Build a public URL for a configured ecosystem R2 lane/key pair."""

    clean_key = _validate_object_key(key) if key else ""
    url = settings.public_url_for_r2_lane(lane, clean_key)
    if not url:
        allowed = [item["lane"] for item in settings.r2_ecosystem_lanes]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "message": "Unknown or unconfigured R2 lane/base URL.",
                "allowed_lanes": allowed,
            },
        )
    return {"ok": True, "lane": lane, "key": clean_key, "public_url": url}


@router.get("/files/r2/{lane}/objects")
def list_r2_lane_objects(
    lane: str,
    prefix: str = Query("", max_length=2048),
    limit: int = Query(100, ge=1, le=1000),
    cursor: str | None = Query(None, max_length=4096),
    search: str | None = Query(None, max_length=255),
    recursive: bool = Query(False),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Browse or search a configured R2 lane through scoped server-side credentials."""

    lane_config = _require_r2_lane(settings, lane, require_read=True)
    clean_prefix = _normalise_prefix(prefix)
    try:
        page = R2Storage(settings).list_objects_page(
            prefix=clean_prefix,
            limit=limit,
            bucket=str(lane_config["bucket"]),
            public_base_url=lane_config.get("public_base_url"),
            cursor=cursor,
            delimiter=None if search or recursive else "/",
            search=search,
            read_only=_r2_read_only_for_lane(lane_config),
            max_scan_keys=settings.r2_multi_bucket_max_scan_keys,
        )
    except RuntimeError as exc:
        return _lane_storage_error_response(
            operation="lane_list",
            lane_config=lane_config,
            key_or_prefix=clean_prefix,
            error=exc,
        )
    return {
        "ok": True,
        "storage": "r2",
        "lane": lane_config["lane"],
        "bucket": lane_config["bucket"],
        "access_mode": lane_config["access_mode"],
        "prefix": clean_prefix,
        "search": search or None,
        "recursive": recursive,
        "count": len(page.objects),
        "prefix_count": len(page.prefixes),
        "prefixes": page.prefixes,
        "files": [asdict(item) for item in page.objects],
        "next_cursor": page.next_cursor,
        "scanned_count": page.scanned_count,
        "truncated": page.truncated,
    }


@router.get("/files/r2/{lane}/metadata")
def r2_lane_object_metadata(
    lane: str,
    key: str = Query(..., min_length=1, max_length=2048),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    lane_config = _require_r2_lane(settings, lane, require_read=True)
    clean_key = _validate_object_key(key)
    try:
        metadata = R2Storage(settings).head_object(
            clean_key,
            bucket=str(lane_config["bucket"]),
            public_base_url=lane_config.get("public_base_url"),
            read_only=_r2_read_only_for_lane(lane_config),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _lane_storage_error_response(
            operation="lane_metadata",
            lane_config=lane_config,
            key_or_prefix=clean_key,
            error=exc,
        )
    return {
        "ok": True,
        "lane": lane_config["lane"],
        "access_mode": lane_config["access_mode"],
        "metadata": asdict(metadata),
        "preview_supported": _text_preview_supported(clean_key, metadata.content_type),
        "chat_supported": _text_preview_supported(clean_key, metadata.content_type),
    }


@router.get("/files/r2/{lane}/read")
def read_r2_lane_object(
    lane: str,
    key: str = Query(..., min_length=1, max_length=2048),
    max_chars: int | None = Query(None, ge=100, le=500_000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    lane_config = _require_r2_lane(settings, lane, require_read=True)
    clean_key = _validate_object_key(key)
    try:
        obj = _read_object_for_lane(
            settings,
            lane_config,
            clean_key,
            max_bytes=settings.max_file_read_bytes,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        return _lane_storage_error_response(
            operation="lane_read",
            lane_config=lane_config,
            key_or_prefix=clean_key,
            error=exc,
        )

    if not _text_preview_supported(clean_key, obj.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="This object can be downloaded but is not supported for text preview or file chat.",
        )
    extracted = _extract_object_text(
        obj,
        settings,
        max_chars=max_chars or settings.document_extract_max_chars,
    )
    return {
        "ok": True,
        "lane": lane_config["lane"],
        "access_mode": lane_config["access_mode"],
        "file": _source_metadata(
            obj,
            settings,
            truncated=bool(extracted.get("truncated")),
            decode_replacements=bool(extracted.get("decode_replacements")),
            lane=str(lane_config["lane"]),
        ),
        "content": extracted.get("content") or "",
        "extraction": extracted.get("extraction"),
    }


@router.get("/files/r2/{lane}/download")
def download_r2_lane_object(
    lane: str,
    key: str = Query(..., min_length=1, max_length=2048),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    lane_config = _require_r2_lane(settings, lane, require_read=True)
    clean_key = _validate_object_key(key)
    try:
        stream = R2Storage(settings).open_object(
            clean_key,
            bucket=str(lane_config["bucket"]),
            max_bytes=settings.r2_download_max_bytes,
            read_only=_r2_read_only_for_lane(lane_config),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    filename = Path(clean_key).name.replace("\r", "_").replace("\n", "_") or "download"
    headers = {
        "Content-Length": str(stream.size_bytes),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "private, no-store",
        "X-HIVE-R2-Lane": str(lane_config["lane"]),
    }
    if stream.etag:
        headers["ETag"] = f'"{stream.etag}"'

    return StreamingResponse(
        _stream_r2_body(stream.body),
        media_type=stream.content_type or "application/octet-stream",
        headers=headers,
    )


@router.get("/files/r2/{lane}/view")
def view_r2_lane_object(
    lane: str,
    key: str = Query(..., min_length=1, max_length=2048),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    lane_config = _require_r2_lane(settings, lane, require_read=True)
    clean_key = _validate_object_key(key)
    try:
        stream = R2Storage(settings).open_object(
            clean_key,
            bucket=str(lane_config["bucket"]),
            max_bytes=settings.r2_download_max_bytes,
            read_only=_r2_read_only_for_lane(lane_config),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    filename = Path(clean_key).name.replace("\r", "_").replace("\n", "_") or "view"
    headers = {
        "Content-Length": str(stream.size_bytes),
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "private, no-store",
        "X-HIVE-R2-Lane": str(lane_config["lane"]),
    }
    if stream.etag:
        headers["ETag"] = f'"{stream.etag}"'

    return StreamingResponse(
        _stream_r2_body(stream.body),
        media_type=stream.content_type or "application/octet-stream",
        headers=headers,
    )


@router.delete("/files/r2/{lane}/objects")
def delete_r2_lane_objects(
    lane: str,
    payload: DeleteR2ObjectsRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Delete one or more objects from a writable R2 lane.

    This is intentionally lane-scoped and server-side only. It refuses read-only
    or registry-only lanes and validates every key before calling R2.
    """

    lane_config = _require_r2_lane(settings, lane, require_read=False, require_write=True)
    clean_keys = _normalise_delete_keys(payload)
    if payload.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "storage": "r2",
            "lane": lane_config["lane"],
            "bucket": lane_config["bucket"],
            "access_mode": lane_config["access_mode"],
            "requested_count": len(clean_keys),
            "object_keys": clean_keys,
        }
    try:
        result = R2Storage(settings).delete_objects(
            clean_keys,
            bucket=str(lane_config["bucket"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _lane_storage_error_response(
            operation="lane_delete",
            lane_config=lane_config,
            key_or_prefix=", ".join(clean_keys[:3]),
            error=exc,
        )
    return {
        "ok": bool(result.get("ok")),
        "storage": "r2",
        "lane": lane_config["lane"],
        "bucket": lane_config["bucket"],
        "access_mode": lane_config["access_mode"],
        **result,
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
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
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
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stored object is not a valid ZIP archive",
        ) from exc
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


@router.post("/files/zip/extract-text")
async def extract_text_from_stored_zip(
    payload: ZipExtractTextRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Extract bounded text from a stored ZIP, optionally chunking/vectorizing it.

    This is the v1.5 free-tier friendly archive workflow. It never extracts the
    entire archive into permanent storage; only a bounded combined text artefact
    is stored so HIVE can chunk/search/ask questions over audit/report ZIPs.
    """

    clean_key = _validate_object_key(payload.object_key)
    try:
        obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_upload_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="zip_extract_read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        temp_path = Path(tmp.name)
        tmp.write(obj.content)

    try:
        report = extract_text_from_zip(
            temp_path,
            max_files=settings.max_zip_files,
            max_uncompressed_bytes=settings.max_zip_uncompressed_bytes,
            max_members=payload.max_members or settings.zip_extract_max_members,
            max_member_bytes=payload.max_member_bytes or settings.zip_extract_max_member_bytes,
            max_total_text_chars=payload.max_total_text_chars
            or settings.zip_extract_max_total_text_chars,
            max_depth=payload.max_depth
            if payload.max_depth is not None
            else settings.zip_extract_max_depth,
            supported_suffixes=settings.zip_extract_supported_suffix_set,
        )
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stored object is not a valid ZIP archive",
        ) from exc
    except UnsafeZipError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    extracted_text = str(report.get("text") or "")
    if not extracted_text.strip():
        return {
            "ok": False,
            "stage": "extract_text",
            "error_code": "no_extractable_text",
            "source": _source_metadata(obj, settings, truncated=False, decode_replacements=False),
            "extraction": {k: v for k, v in report.items() if k != "text"},
        }

    source_name = Path(clean_key).stem or "archive"
    text_result = ingest_text_content(
        filename=f"{source_name}-extracted.txt",
        content=extracted_text,
        content_type="text/plain; charset=utf-8",
        settings=settings,
    )
    metadata = {
        "test_run_id": payload.test_run_id,
        "source_archive_object_key": clean_key,
        "extraction_summary": report.get("summary"),
        "extraction_limits": report.get("limits"),
        "free_tier_mode": settings.hive_free_tier_mode,
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}
    db_file = SqlStore(settings).record_file(text_result, extra_metadata=metadata)

    chunk_result: dict[str, object] | None = None
    vectorize_result: dict[str, object] | None = None
    if payload.chunk:
        chunks = split_text_into_chunks(
            extracted_text,
            max_chars=settings.file_chunk_max_chars,
            overlap_chars=settings.file_chunk_overlap_chars,
            max_chunks=settings.file_chunk_max_count,
        )
        chunk_dicts = chunks_to_dicts(chunks)
        chunk_source = {
            **metadata,
            "object_key": text_result.object_key,
            "source_archive_object_key": clean_key,
            "extracted_text_chars": len(extracted_text),
            "zip_extracted_items": (report.get("summary") or {}).get("extracted_count"),
        }
        chunk_result = SqlStore(settings).record_file_chunks(
            object_key=text_result.object_key,
            chunks=chunk_dicts,
            source_metadata=chunk_source,
            replace_existing=payload.replace_existing_chunks,
        )
        if payload.vectorize and chunk_result.get("ok"):
            stored_chunks = SqlStore(settings).list_file_chunks(
                object_key=text_result.object_key,
                limit=settings.file_chunk_max_count,
                include_content=True,
            )
            vectorize_result = await _vectorize_chunks(
                chunks=stored_chunks.get("chunks", []) if isinstance(stored_chunks, dict) else [],
                object_key=text_result.object_key,
                settings=settings,
                test_run_id=payload.test_run_id,
            )

    return {
        "ok": True,
        "stage": "complete",
        "source_archive": _source_metadata(
            obj, settings, truncated=False, decode_replacements=False
        ),
        "extracted_file": text_result.__dict__,
        "extraction": {k: v for k, v in report.items() if k != "text"},
        "extracted_text_preview": extracted_text[:1200],
        "db_recorded": bool(db_file.get("ok")),
        "db_error": db_file.get("error"),
        "chunking": chunk_result,
        "vectorize": vectorize_result,
        "free_tier": {
            "enabled": settings.hive_free_tier_mode,
            "note": "Archive extraction is bounded for Koyeb free web-service safety.",
        },
    }


@router.post("/files/chunk")
def chunk_file(
    payload: FileChunkRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Read one stored text-ish object, split it into SQL-backed retrieval chunks."""

    clean_key = _validate_object_key(payload.object_key)
    try:
        obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="chunk_read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    extracted = _extract_object_text(obj, settings, max_chars=settings.document_extract_max_chars)
    content = extracted["content"]
    had_decode_replacements = bool(extracted.get("decode_replacements"))
    max_chars = payload.max_chars or settings.file_chunk_max_chars
    overlap_chars = (
        payload.overlap_chars
        if payload.overlap_chars is not None
        else settings.file_chunk_overlap_chars
    )
    max_chunks = payload.max_chunks or settings.file_chunk_max_count
    chunks = split_text_into_chunks(
        content,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        max_chunks=max_chunks,
    )
    chunk_dicts = chunks_to_dicts(chunks)
    source = _source_metadata(
        obj,
        settings,
        truncated=bool(extracted.get("truncated")),
        decode_replacements=had_decode_replacements,
    )
    source["extraction"] = extracted.get("extraction")
    if payload.test_run_id:
        source["test_run_id"] = payload.test_run_id
    db_record = SqlStore(settings).record_file_chunks(
        object_key=clean_key,
        chunks=chunk_dicts,
        source_metadata=source,
        replace_existing=payload.replace_existing,
    )
    return {
        "ok": bool(db_record.get("ok")),
        "source": source,
        "chunking": {
            "chunk_count": len(chunk_dicts),
            "max_chars": max_chars,
            "overlap_chars": overlap_chars,
            "max_chunks": max_chunks,
            "replace_existing": payload.replace_existing,
            "total_token_estimate": sum(
                int(chunk.get("token_estimate") or 0) for chunk in chunk_dicts
            ),
        },
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
        "chunks_preview": [
            {**chunk, "content": chunk["content"][:360]} for chunk in chunk_dicts[:5]
        ],
    }


@router.get("/files/chunks/search")
def search_chunks(
    query: str = Query(..., min_length=1, max_length=2000),
    key: str | None = Query(None, min_length=1, max_length=2048),
    limit: int = Query(6, ge=1, le=30),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Search persisted file chunks with SQL-backed lexical retrieval."""

    clean_key = _validate_object_key(key) if key else None
    return SqlStore(settings).search_file_chunks(query=query, object_key=clean_key, limit=limit)


@router.get("/files/chunks")
def list_chunks(
    key: str = Query(..., min_length=1, max_length=2048),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_content: bool = Query(False),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List persisted SQL chunks for one file."""

    clean_key = _validate_object_key(key)
    return SqlStore(settings).list_file_chunks(
        object_key=clean_key,
        limit=limit,
        offset=offset,
        include_content=include_content,
    )


@router.post("/files/vectorize")
async def vectorize_file_chunks(
    payload: FileVectorizeRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Embed persisted SQL chunks and upsert them to Cloudflare Vectorize.

    Vector IDs are the SQL chunk IDs, so PostgreSQL remains the source of truth.
    """

    clean_key = _validate_object_key(payload.object_key)
    if payload.auto_chunk:
        existing = SqlStore(settings).list_file_chunks(
            object_key=clean_key,
            limit=1,
            include_content=False,
        )
        if not isinstance(existing, dict) or not existing.get("ok"):
            return {
                "ok": False,
                "stage": "chunk_check",
                "error": existing.get("error")
                if isinstance(existing, dict)
                else "Chunk lookup failed.",
            }
        if existing.get("count") == 0 or payload.replace_existing_chunks:
            chunk_result = _chunk_file_for_chat(clean_key=clean_key, settings=settings)
            if not chunk_result.get("ok"):
                return {"ok": False, "stage": "auto_chunk", "chunking": chunk_result}

    chunk_limit = payload.chunk_limit or settings.file_chunk_max_count
    chunks_result = SqlStore(settings).list_file_chunks(
        object_key=clean_key,
        limit=chunk_limit,
        include_content=True,
    )
    chunks = chunks_result.get("chunks", []) if isinstance(chunks_result, dict) else []
    if not isinstance(chunks_result, dict) or not chunks_result.get("ok"):
        return {
            "ok": False,
            "stage": "list_chunks",
            "error": chunks_result.get("error")
            if isinstance(chunks_result, dict)
            else "Chunk list failed.",
        }
    if not chunks:
        return {
            "ok": False,
            "stage": "list_chunks",
            "error_code": "no_chunks_found",
            "message": "No persisted chunks found to vectorize.",
        }

    return await _vectorize_chunks(
        chunks=chunks,
        object_key=clean_key,
        settings=settings,
        batch_size=payload.batch_size,
        test_run_id=payload.test_run_id,
    )


@router.get("/files/vector-search")
async def vector_search_file_chunks(
    query: str = Query(..., min_length=1, max_length=2000),
    key: str | None = Query(None, min_length=1, max_length=2048),
    limit: int = Query(6, ge=1, le=30),
    fallback_sql: bool = Query(True),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Search chunks using Vectorize, falling back to SQL lexical search by default."""

    clean_key = _validate_object_key(key) if key else None
    return await _vector_search_chunks(
        query=query,
        object_key=clean_key,
        limit=limit,
        settings=settings,
        fallback_sql=fallback_sql,
    )


@router.post("/chat/with-file")
async def chat_with_file(
    request: ChatWithFileRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Ask a question about one or more stored R2/local objects.

    A request may use the legacy ``lane`` + ``object_key`` shape or the newer
    ``files`` array. The latter supports multiple selected R2 objects across
    different governed lanes/buckets while keeping object reads lane-scoped and
    bounded.
    """

    workflow_preset = get_workflow_preset(request.workflow_preset)
    if request.workflow_preset and workflow_preset is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Unknown workflow_preset.",
                "workflow_preset": request.workflow_preset,
                "allowed_presets": allowed_workflow_presets(),
            },
        )
    if workflow_preset is not None:
        request = apply_workflow_preset_to_request(request, workflow_preset)
    workflow_metadata = workflow_preset.safe_dict() if workflow_preset else None

    timings: dict[str, float | None] = {
        "validate_key_seconds": None,
        "read_file_seconds": None,
        "decode_seconds": None,
        "chunk_retrieval_seconds": None,
        "chunk_index_seconds": None,
        "prompt_build_seconds": None,
        "model_call_seconds": None,
        "db_record_seconds": None,
        "total_seconds": None,
    }
    total_started = time.perf_counter()
    stage = "validate_sources"

    try:
        source_refs = _time_stage(
            timings,
            "validate_key_seconds",
            lambda: _request_file_refs(request),
        )
        primary_ref = source_refs[0]
        clean_key = primary_ref.object_key
        request.object_key = clean_key
        request.lane = primary_ref.lane
        lane_config = _require_r2_lane(
            settings,
            primary_ref.lane,
            require_read=(primary_ref.lane.strip().lower().replace("-", "_") != "uploads"),
        )
    except HTTPException:
        raise

    source_label = primary_ref.name or Path(clean_key).name or clean_key
    multi_source = len(source_refs) > 1
    use_persisted_chunks = bool(
        request.use_chunks and lane_config["primary_upload_lane"] and not multi_source
    )
    chunk_mode_note = None
    if request.use_chunks and multi_source:
        chunk_mode_note = "Persisted chunk retrieval is limited to one uploads-lane file; selected R2 files used bounded direct reads."
    elif request.use_chunks and not use_persisted_chunks:
        chunk_mode_note = "Persisted chunk retrieval is limited to the uploads lane; this lane used a bounded direct read."

    chunks: list[dict[str, object]] = []
    if use_persisted_chunks:
        stage = "chunk_retrieval"
        query = request.chunk_query or request.message
        chunk_limit = request.chunk_limit or settings.file_retrieval_max_chunks
        store = SqlStore(settings)

        retrieval_started = time.perf_counter()
        if request.use_vectorize:
            retrieval = await _vector_search_chunks(
                query=query,
                object_key=clean_key,
                limit=chunk_limit,
                settings=settings,
                fallback_sql=request.vectorize_fallback_sql,
            )
        else:
            retrieval = store.search_file_chunks(query=query, object_key=clean_key, limit=chunk_limit)
            if isinstance(retrieval, dict):
                retrieval["retrieval_mode"] = "sql"
        timings["chunk_retrieval_seconds"] = round(time.perf_counter() - retrieval_started, 3)
        chunks = retrieval.get("chunks", []) if isinstance(retrieval, dict) else []

        if not chunks and request.auto_chunk:
            stage = "chunk_index"
            try:
                chunk_index_started = time.perf_counter()
                chunk_index_result = await asyncio.to_thread(
                    _chunk_file_for_chat,
                    clean_key=clean_key,
                    settings=settings,
                )
                timings["chunk_index_seconds"] = round(time.perf_counter() - chunk_index_started, 3)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
                ) from exc
            except RuntimeError as exc:
                response = _storage_error_response(
                    operation="chat_with_file_auto_chunk_read",
                    settings=settings,
                    key_or_prefix=clean_key,
                    error=exc,
                )
                response["stage"] = stage
                response["timings"] = _finalise_timings(timings, total_started)
                return response
            if not chunk_index_result.get("ok"):
                return {
                    "ok": False,
                    "stage": stage,
                    "error_code": "chunk_index_failed",
                    "message": chunk_index_result.get("db_error") or "File chunks could not be recorded.",
                    "chunk_index": chunk_index_result,
                    "timings": _finalise_timings(timings, total_started),
                }
            stage = "chunk_retrieval"
            retrieval_started = time.perf_counter()
            if request.use_vectorize:
                retrieval = await _vector_search_chunks(
                    query=query,
                    object_key=clean_key,
                    limit=chunk_limit,
                    settings=settings,
                    fallback_sql=request.vectorize_fallback_sql,
                )
            else:
                retrieval = store.search_file_chunks(query=query, object_key=clean_key, limit=chunk_limit)
                if isinstance(retrieval, dict):
                    retrieval["retrieval_mode"] = "sql"
            timings["chunk_retrieval_seconds"] = round(time.perf_counter() - retrieval_started, 3)
            chunks = retrieval.get("chunks", []) if isinstance(retrieval, dict) else []

        if not isinstance(retrieval, dict) or not retrieval.get("ok"):
            return {
                "ok": False,
                "stage": stage,
                "error_code": "chunk_retrieval_failed",
                "message": (retrieval or {}).get("error") if isinstance(retrieval, dict) else "Chunk retrieval failed.",
                "retrieval": retrieval if isinstance(retrieval, dict) else None,
                "timings": _finalise_timings(timings, total_started),
                "hint": "Run POST /v1/files/chunk first, retry with use_chunks=false for small files, or enable SQL fallback for Vectorize.",
            }
        if not chunks:
            return {
                "ok": False,
                "stage": stage,
                "error_code": "no_chunks_found",
                "message": "No persisted chunks were found for this file/query.",
                "retrieval": retrieval,
                "timings": _finalise_timings(timings, total_started),
                "hint": "Run POST /v1/files/chunk for this object_key, or set auto_chunk=true.",
            }

        excerpt = _chunks_to_excerpt(chunks)
        obj = _object_stub_from_chunks(clean_key, settings)
        source = _chunk_source_metadata(clean_key, settings, retrieval, chunks)
        source["lane"] = "uploads"
        retrieval_metadata = _retrieval_metadata(retrieval, chunks)
        truncated = False
        sources = [source]
        source_citations = [
            {
                "label": source_label,
                "object_key": obj.key,
                "public_url": obj.public_url,
                "lane": lane_config["lane"],
            }
        ]
    else:
        stage = "read_file"
        try:
            read_started = time.perf_counter()
            direct_context = await asyncio.to_thread(
                _read_file_sources_for_chat,
                settings=settings,
                source_refs=source_refs,
                max_chars=request.max_file_chars or settings.max_file_chat_chars,
            )
            timings["read_file_seconds"] = round(time.perf_counter() - read_started, 3)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            response = _lane_storage_error_response(
                operation="chat_with_file_read",
                lane_config=lane_config,
                key_or_prefix=clean_key,
                error=exc,
            )
            response["stage"] = stage
            response["timings"] = _finalise_timings(timings, total_started)
            return response

        stage = "decode_file"
        timings["decode_seconds"] = direct_context["decode_seconds"]
        obj = direct_context["obj"]
        source = direct_context["source"]
        sources = direct_context["sources"]
        source_citations = direct_context["source_citations"]
        source_label = str(direct_context["source_label"])
        excerpt = str(direct_context["excerpt"])
        truncated = bool(direct_context["truncated"])
        retrieval_metadata = None

    primary_source_citation = source_citations[0]
    if len(source_citations) > 1:
        primary_source_citation = {
            "label": f"{len(source_citations)} attached files",
            "object_key": None,
            "public_url": None,
            "lane": "multiple",
        }

    stage = "prompt_build"
    prompt_context = _time_stage(
        timings,
        "prompt_build_seconds",
        lambda: _build_file_chat_payload(
            request=request,
            settings=settings,
            source_label=source_label,
            obj=obj,
            excerpt=excerpt,
            truncated=truncated,
            sources=sources,
        ),
    )
    payload = prompt_context["payload"]
    fallback_models = prompt_context["fallback_models"]
    selected_model = prompt_context["selected_model"]
    effective_mode = prompt_context["effective_mode"]

    if request.skip_model or request.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "skip_model": True,
            "stage": "complete_without_model",
            "message": "File read and prompt build completed; model call was skipped.",
            "selected_model": selected_model,
            "fallback_models": fallback_models,
            "effective_mode": str(effective_mode),
            "workflow_preset": workflow_metadata,
            "selected_skill": prompt_context.get("selected_skill"),
            "prompt_message_count": len(payload.get("messages", [])),
            "file_count": len(source_citations),
            "file_excerpt_chars": len(excerpt),
            "source": source,
            "sources": sources,
            "retrieval_metadata": retrieval_metadata,
            "retrieval_summary": _retrieval_summary(retrieval_metadata, workflow_metadata),
            "source_chunks": _source_chunks_metadata(chunks if use_persisted_chunks else []),
            "chunk_mode_note": chunk_mode_note,
            "source_citation": primary_source_citation,
            "source_citations": source_citations,
            "timings": _finalise_timings(timings, total_started),
        }

    stage = "model_call"
    client = OpenRouterClient(settings)
    model_timeout = request.model_timeout_seconds or settings.chat_with_file_model_timeout_seconds
    model_started = time.perf_counter()
    try:
        completion = await asyncio.wait_for(
            client.chat_completion(payload, fallback_models=fallback_models),
            timeout=max(0.5, float(model_timeout)),
        )
    except TimeoutError:
        timings["model_call_seconds"] = round(time.perf_counter() - model_started, 3)
        return _chat_with_file_model_error_response(
            stage=stage,
            error_code="chat_with_file_timeout",
            message=f"Model call timed out after {model_timeout} seconds.",
            payload=payload,
            selected_model=selected_model,
            fallback_models=fallback_models,
            source=source,
            sources=sources,
            source_label=source_label,
            source_citations=source_citations,
            obj=obj,
            timings=_finalise_timings(timings, total_started),
            retrieval_metadata=retrieval_metadata,
            lane=str(lane_config["lane"]),
        )
    except Exception as exc:  # defensive: never let this endpoint become a vague 502
        timings["model_call_seconds"] = round(time.perf_counter() - model_started, 3)
        return _chat_with_file_model_error_response(
            stage=stage,
            error_code="chat_with_file_model_error",
            message=str(exc),
            payload=payload,
            selected_model=selected_model,
            fallback_models=fallback_models,
            source=source,
            sources=sources,
            source_label=source_label,
            source_citations=source_citations,
            obj=obj,
            timings=_finalise_timings(timings, total_started),
            retrieval_metadata=retrieval_metadata,
            lane=str(lane_config["lane"]),
        )
    timings["model_call_seconds"] = round(time.perf_counter() - model_started, 3)

    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reply = _reply_text(message.get("content"))
    finish_reason = choice.get("finish_reason")
    empty_reply = not reply.strip()
    ok = not bool(completion.get("_all_attempts_failed")) and not empty_reply
    model_used = completion.get("model") or payload.get("model")
    provider = completion.get("provider")
    usage = completion.get("usage")

    stage = "db_record"
    db_started = time.perf_counter()
    db_record = await asyncio.to_thread(
        SqlStore(settings).record_chat,
        conversation_id=request.conversation_id,
        mode=str(request.mode),
        user_message=request.message,
        assistant_reply=reply,
        model_used=str(model_used) if model_used else None,
        provider=str(provider) if provider else None,
        usage=usage if isinstance(usage, dict) else None,
        metadata={
            "endpoint": "/v1/chat/with-file",
            "ok": ok,
            "finish_reason": finish_reason,
            "empty_reply": empty_reply,
            "source": source,
            "sources": sources,
            "source_citations": source_citations,
            "retrieval_metadata": retrieval_metadata,
            "workflow_preset": workflow_metadata,
            "selected_skill": prompt_context.get("selected_skill"),
            "timings": timings,
            "test_run_id": request.test_run_id,
        },
    )
    timings["db_record_seconds"] = round(time.perf_counter() - db_started, 3)

    return {
        "ok": ok,
        "reply": reply,
        "model_used": model_used,
        "provider": provider,
        "usage": usage,
        "raw_finish_reason": finish_reason,
        "completion_truncated": finish_reason == "length",
        "empty_reply": empty_reply,
        "error_code": completion.get("hive_error_code"),
        "attempts": completion.get("hive_attempts"),
        "stage": "complete",
        "workflow_preset": workflow_metadata,
        "selected_skill": prompt_context.get("selected_skill"),
        "timings": _finalise_timings(timings, total_started),
        "conversation_id": db_record.get("conversation_id") or request.conversation_id,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
        "file_count": len(source_citations),
        "source": source,
        "sources": sources,
        "retrieval_metadata": retrieval_metadata,
        "retrieval_summary": _retrieval_summary(retrieval_metadata, workflow_metadata),
        "source_chunks": _source_chunks_metadata(chunks if use_persisted_chunks else []),
        "chunk_mode_note": chunk_mode_note,
        "source_citation": primary_source_citation,
        "source_citations": source_citations,
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


async def _vectorize_chunks(
    *,
    chunks: list[dict[str, object]],
    object_key: str,
    settings: Settings,
    batch_size: int | None = None,
    test_run_id: str | None = None,
) -> dict[str, object]:
    embeddings = CloudflareEmbeddingsClient(settings)
    vectorize = VectorizeClient(settings)
    if not embeddings.enabled:
        return {
            "ok": False,
            "stage": "embeddings",
            "embeddings": embeddings.safe_config,
            "error": "Embeddings disabled or not configured.",
        }
    if not vectorize.enabled:
        return {
            "ok": False,
            "stage": "vectorize",
            "vectorize": vectorize.safe_config,
            "error": "Vectorize disabled or not configured.",
        }

    clean_chunks = [
        chunk
        for chunk in chunks
        if chunk.get("id") and (chunk.get("content") or chunk.get("content_preview"))
    ]
    if not clean_chunks:
        return {
            "ok": False,
            "stage": "prepare",
            "error": "No chunk IDs/content available for vectorization.",
        }

    requested_batch_size = batch_size or settings.embeddings_max_batch_size
    safe_batch_size = max(
        1, min(int(requested_batch_size), int(settings.embeddings_max_batch_size), 100)
    )
    upserts: list[dict[str, object]] = []
    embedding_batches = 0
    vector_count = 0
    mutation_ids: list[str] = []

    for batch in _batches(clean_chunks, safe_batch_size):
        texts = [str(chunk.get("content") or chunk.get("content_preview") or "") for chunk in batch]
        embedding_result = await embeddings.embed_texts(texts)
        embedding_batches += 1
        if not embedding_result.get("ok"):
            return {
                "ok": False,
                "stage": "embedding",
                "batch": embedding_batches,
                "embedding_error": embedding_result,
            }
        vectors = embedding_result.get("vectors") or []
        vector_payload: list[dict[str, object]] = []
        for chunk, vector in zip(batch, vectors):
            chunk_id = str(chunk.get("id"))
            metadata = {
                "object_key": object_key,
                "chunk_id": chunk_id,
                "chunk_index": chunk.get("chunk_index"),
                "content_sha256": chunk.get("content_sha256"),
                "source": "hive_sql_chunk",
            }
            if test_run_id:
                metadata["test_run_id"] = test_run_id
            vector_payload.append({"id": chunk_id, "values": vector, "metadata": metadata})
        upsert_result = await vectorize.upsert_vectors(vector_payload)
        if not upsert_result.get("ok"):
            return {
                "ok": False,
                "stage": "upsert",
                "batch": embedding_batches,
                "vectorize_error": upsert_result,
            }
        raw_result = (
            upsert_result.get("result") if isinstance(upsert_result.get("result"), dict) else {}
        )
        mutation_id = raw_result.get("mutationId") if isinstance(raw_result, dict) else None
        if mutation_id:
            mutation_ids.append(str(mutation_id))
        vector_count += len(vector_payload)
        upserts.append(
            {
                "batch": embedding_batches,
                "count": len(vector_payload),
                "mutation_id": mutation_id,
                "status_code": upsert_result.get("status_code"),
            }
        )

    return {
        "ok": True,
        "stage": "complete",
        "object_key": object_key,
        "chunk_count": len(clean_chunks),
        "vector_count": vector_count,
        "batch_count": embedding_batches,
        "mutation_ids": mutation_ids,
        "upserts": upserts,
        "vectorize": vectorize.safe_config,
        "embeddings": embeddings.safe_config,
        "note": "Vectorize upserts are asynchronous; allow a short delay before querying newly upserted vectors.",
    }


async def _vector_search_chunks(
    *,
    query: str,
    object_key: str | None,
    limit: int,
    settings: Settings,
    fallback_sql: bool = True,
) -> dict[str, object]:
    embeddings = CloudflareEmbeddingsClient(settings)
    vectorize = VectorizeClient(settings)
    vector_diag: dict[str, object] = {
        "vectorize_enabled": vectorize.enabled,
        "embeddings_enabled": embeddings.enabled,
        "index_name": settings.vectorize_index_name,
    }

    if embeddings.enabled and vectorize.enabled:
        embedding = await embeddings.embed_texts([query])
        if embedding.get("ok") and embedding.get("vectors"):
            query_result = await vectorize.query(
                embedding["vectors"][0],
                top_k=max(limit, int(settings.vectorize_top_k)),
            )
            vector_diag["query"] = {
                k: v for k, v in query_result.items() if k not in {"raw", "matches"}
            }
            matches = query_result.get("matches") or []
            ids = [str(match.get("id")) for match in matches if match.get("id")]
            if ids:
                sql_result = SqlStore(settings).get_file_chunks_by_ids(
                    chunk_ids=ids,
                    object_key=object_key,
                    include_content=True,
                )
                if sql_result.get("ok"):
                    score_by_id = {str(match.get("id")): match.get("score") for match in matches}
                    chunks = sql_result.get("chunks", [])
                    for chunk in chunks:
                        chunk["vector_score"] = score_by_id.get(str(chunk.get("id")))
                        chunk["score"] = chunk.get("vector_score")
                    if chunks:
                        return {
                            "ok": True,
                            "enabled": True,
                            "retrieval_mode": "vectorize",
                            "retrieval_source": "vectorize",
                            "fallback_used": False,
                            "vector_hits": len(chunks),
                            "sql_fallback_hits": 0,
                            "query": query,
                            "object_key": object_key,
                            "candidate_count": len(matches),
                            "count": len(chunks),
                            "chunks": chunks[:limit],
                            "vectorize": vector_diag,
                        }
                vector_diag["sql_lookup"] = sql_result
            else:
                vector_diag["empty_matches"] = True
        else:
            vector_diag["embedding_error"] = embedding
    else:
        vector_diag["reason"] = "Vectorize or embeddings disabled/not configured."

    if fallback_sql:
        fallback = SqlStore(settings).search_file_chunks(
            query=query, object_key=object_key, limit=limit
        )
        if isinstance(fallback, dict):
            fallback["retrieval_mode"] = "sql_fallback"
            fallback["retrieval_source"] = "sql_fallback"
            fallback["fallback_used"] = True
            fallback["vector_hits"] = 0
            fallback["sql_fallback_hits"] = len(fallback.get("chunks") or [])
            fallback["vectorize"] = vector_diag
        return fallback

    return {
        "ok": False,
        "enabled": True,
        "retrieval_mode": "vectorize",
        "retrieval_source": "vectorize",
        "fallback_used": False,
        "vector_hits": 0,
        "sql_fallback_hits": 0,
        "query": query,
        "object_key": object_key,
        "count": 0,
        "chunks": [],
        "vectorize": vector_diag,
        "error": "Vectorize retrieval produced no usable chunks and SQL fallback is disabled.",
    }


def _batches(items: list[dict[str, object]], batch_size: int) -> list[list[dict[str, object]]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _time_stage(timings: dict[str, float | None], key: str, func):  # noqa: ANN001, ANN202
    started = time.perf_counter()
    try:
        return func()
    finally:
        timings[key] = round(time.perf_counter() - started, 3)


def _finalise_timings(
    timings: dict[str, float | None], total_started: float
) -> dict[str, float | None]:
    final = dict(timings)
    final["total_seconds"] = round(time.perf_counter() - total_started, 3)
    return final


def _selected_skill_context(request: ChatWithFileRequest, settings: Settings) -> dict[str, object] | None:
    skill_id = (request.skill_id or "").strip()
    fallback_title = (request.skill_title or skill_id).strip()
    if not skill_id:
        return None
    try:
        lookup = get_skill_catalogue_item(settings=settings, skill_id=skill_id)
    except Exception as exc:  # defensive: a skill lookup must not break file chat
        return {
            "id": skill_id,
            "title": fallback_title or skill_id,
            "description": "",
            "lookup_ok": False,
            "lookup_error": str(exc),
        }
    if not lookup.get("ok"):
        return {
            "id": skill_id,
            "title": fallback_title or skill_id,
            "description": "",
            "lookup_ok": False,
            "error_code": lookup.get("error_code"),
        }
    item = lookup.get("item") if isinstance(lookup.get("item"), dict) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "id": str(item.get("id") or metadata.get("skill_id") or skill_id),
        "source_id": str(item.get("source_id") or metadata.get("skill_id") or skill_id),
        "title": str(item.get("title") or metadata.get("name") or fallback_title or skill_id),
        "description": str(metadata.get("description") or item.get("description") or ""),
        "repo": metadata.get("repo") or metadata.get("repos"),
        "hive_lane": metadata.get("hive_lane"),
        "risk_level": metadata.get("risk_level"),
        "priority_tier": metadata.get("priority_tier"),
        "tags": metadata.get("tags"),
        "descriptor_url": metadata.get("descriptor_url") or item.get("url"),
        "lookup_ok": True,
    }


def _selected_skill_instruction(selected_skill: dict[str, object] | None) -> str | None:
    if not selected_skill:
        return None
    title = str(selected_skill.get("title") or selected_skill.get("id") or "selected skill")
    parts = [
        "Apply the selected existing HIVE skill to the attached file.",
        f"Skill: {title}",
    ]
    for label, key in [
        ("Description", "description"),
        ("Lane", "hive_lane"),
        ("Risk", "risk_level"),
        ("Priority", "priority_tier"),
        ("Descriptor", "descriptor_url"),
    ]:
        value = selected_skill.get(key)
        if value:
            parts.append(f"{label}: {value}")
    parts.append(
        "Use the skill as guidance only. Do not claim the skill executed external tools unless the response explicitly reports an approved execution path."
    )
    return "\n".join(parts)


def _request_file_refs(request: ChatWithFileRequest) -> list[FileReference]:
    raw_refs: list[FileReference] = []
    if request.object_key:
        raw_refs.append(
            FileReference(
                lane=request.lane or "uploads",
                object_key=request.object_key,
                name=None,
            )
        )
    raw_refs.extend(request.files or [])

    refs: list[FileReference] = []
    seen: set[tuple[str, str]] = set()
    for ref in raw_refs:
        clean_lane = ref.lane.strip().lower().replace("-", "_") or "uploads"
        clean_key = _validate_object_key(ref.object_key)
        identity = (clean_lane, clean_key)
        if identity in seen:
            continue
        seen.add(identity)
        refs.append(FileReference(lane=clean_lane, object_key=clean_key, name=ref.name))
    if not refs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file object_key or files[] entry is required.",
        )
    if len(refs) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File chat is limited to 20 selected files per request.",
        )
    return refs


def _normalise_delete_keys(payload: DeleteR2ObjectsRequest) -> list[str]:
    raw_keys: list[str] = []
    if payload.object_key:
        raw_keys.append(payload.object_key)
    raw_keys.extend(payload.object_keys or [])
    clean_keys: list[str] = []
    seen: set[str] = set()
    for raw_key in raw_keys:
        clean_key = _validate_object_key(str(raw_key))
        if clean_key in seen:
            continue
        seen.add(clean_key)
        clean_keys.append(clean_key)
    if not clean_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one object key is required for deletion.",
        )
    if len(clean_keys) > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="R2 delete is limited to 1000 objects per request.",
        )
    return clean_keys


def _read_file_sources_for_chat(
    *,
    settings: Settings,
    source_refs: list[FileReference],
    max_chars: int,
) -> dict[str, object]:
    total_budget = max(1000, min(int(max_chars), settings.document_extract_max_chars))
    per_file_budget = max(1000, total_budget // max(1, len(source_refs)))
    remaining_budget = total_budget
    decode_seconds = 0.0
    sources: list[dict[str, object]] = []
    citations: list[dict[str, object]] = []
    blocks: list[str] = []
    primary_obj = None
    truncated_any = False

    for index, ref in enumerate(source_refs, start=1):
        lane_config = _require_r2_lane(
            settings,
            ref.lane,
            require_read=(ref.lane.strip().lower().replace("-", "_") != "uploads"),
        )
        obj = _read_object_for_lane(
            settings,
            lane_config,
            ref.object_key,
            max_bytes=settings.max_file_read_bytes,
        )
        if primary_obj is None:
            primary_obj = obj

        decode_started = time.perf_counter()
        extracted = _extract_object_text(
            obj,
            settings,
            max_chars=min(settings.document_extract_max_chars, per_file_budget),
        )
        decode_seconds += time.perf_counter() - decode_started

        content = str(extracted.get("content") or "")
        allowed_chars = max(0, min(per_file_budget, remaining_budget))
        excerpt = content[:allowed_chars] if allowed_chars else ""
        omitted_for_budget = bool(content and not excerpt)
        source_truncated = (len(content) > len(excerpt)) or bool(extracted.get("truncated"))
        truncated_any = truncated_any or source_truncated or omitted_for_budget
        remaining_budget = max(0, remaining_budget - len(excerpt))

        label = ref.name or Path(ref.object_key).name or ref.object_key
        source = _source_metadata(
            obj,
            settings,
            truncated=source_truncated,
            decode_replacements=bool(extracted.get("decode_replacements")),
            lane=str(lane_config["lane"]),
        )
        source["extraction"] = extracted.get("extraction")
        source["label"] = label
        sources.append(source)
        citations.append(
            {
                "label": label,
                "object_key": obj.key,
                "public_url": obj.public_url,
                "lane": lane_config["lane"],
            }
        )

        if omitted_for_budget:
            body = "[Excerpt omitted because the shared multi-file context budget was exhausted.]"
        else:
            body = excerpt
        if source_truncated and excerpt:
            body = f"{body}\n[THIS FILE EXCERPT WAS TRUNCATED]"
        blocks.append(
            "\n".join(
                [
                    f"[File {index}: {label}]",
                    f"Lane: {lane_config['lane']}",
                    f"Content type: {obj.content_type or 'unknown'}",
                    f"Size bytes: {obj.size_bytes}",
                    "",
                    body,
                ]
            ).strip()
        )

    if primary_obj is None:  # defensive; _request_file_refs prevents this
        raise ValueError("At least one file is required")

    source_label = (
        str(citations[0]["label"])
        if len(citations) == 1
        else f"{len(citations)} selected files"
    )
    return {
        "obj": primary_obj,
        "source": sources[0],
        "sources": sources,
        "source_citations": citations,
        "source_label": source_label,
        "excerpt": "\n\n---\n\n".join(block for block in blocks if block),
        "truncated": truncated_any,
        "decode_seconds": round(decode_seconds, 3),
    }


def _build_file_chat_payload(
    *,
    request: ChatWithFileRequest,
    settings: Settings,
    source_label: str,
    obj,
    excerpt: str,
    truncated: bool,
    sources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    effective_mode = router_service.resolve_mode(task, request.mode)
    selected_model = router_service.select_model(task, request.model)
    fallback_models = router_service.fallback_models_for_task(task, selected_model)

    source_items = sources or []
    multiple_sources = len(source_items) > 1
    if multiple_sources:
        source_lines = [
            f"- [{index + 1}] {Path(str(item.get('object_key') or '')).name or item.get('object_key')} "
            f"(lane: {item.get('lane')}, content type: {item.get('content_type') or 'unknown'}, "
            f"size bytes: {item.get('size_bytes')})"
            for index, item in enumerate(source_items)
        ]
        attachment_intro = [
            f"Attached file set: {source_label}",
            f"Attached file count: {len(source_items)}",
            "Attached files:",
            *source_lines,
            "",
            "File content excerpts:",
        ]
    else:
        attachment_intro = [
            f"Attached file label: {source_label}",
            f"Content type: {obj.content_type or 'unknown'}",
            f"Size bytes: {obj.size_bytes}",
            "",
            "File content excerpt:",
        ]

    window = ContextWindow()
    window.add("system", build_system_prompt(effective_mode))
    window.add(
        "system",
        "Answer using the attached file content only where relevant. "
        "Do not print object keys or public URLs inside the answer; "
        "the API returns source metadata separately. When multiple files are attached, "
        "identify the file label used for each important finding.",
    )
    preset = get_workflow_preset(request.workflow_preset)
    if preset:
        window.add(
            "system",
            "Workflow preset: "
            f"{preset.name}. {preset.prompt_instruction} "
            f"Preferred output shape: {preset.output_shape}.",
        )
    selected_skill = _selected_skill_context(request, settings)
    selected_skill_instruction = _selected_skill_instruction(selected_skill)
    if selected_skill_instruction:
        window.add("system", selected_skill_instruction)
    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add(
        "user",
        "\n".join(
            [
                request.message,
                "",
                *attachment_intro,
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
    return {
        "payload": payload,
        "fallback_models": fallback_models,
        "selected_model": selected_model,
        "effective_mode": effective_mode,
        "selected_skill": selected_skill,
    }


def _chunk_file_for_chat(*, clean_key: str, settings: Settings) -> dict[str, object]:
    obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes)
    extracted = _extract_object_text(obj, settings, max_chars=settings.document_extract_max_chars)
    content = str(extracted.get("content") or "")
    had_decode_replacements = bool(extracted.get("decode_replacements"))
    chunks = split_text_into_chunks(
        content,
        max_chars=settings.file_chunk_max_chars,
        overlap_chars=settings.file_chunk_overlap_chars,
        max_chunks=settings.file_chunk_max_count,
    )
    source = _source_metadata(
        obj,
        settings,
        truncated=bool(extracted.get("truncated")),
        decode_replacements=had_decode_replacements,
    )
    source["extraction"] = extracted.get("extraction")
    db_record = SqlStore(settings).record_file_chunks(
        object_key=clean_key,
        chunks=chunks_to_dicts(chunks),
        source_metadata=source,
        replace_existing=True,
    )
    return {
        "ok": bool(db_record.get("ok")),
        "object_key": clean_key,
        "chunk_count": len(chunks),
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
    }


def _chunks_to_excerpt(chunks: list[dict[str, object]]) -> str:
    blocks: list[str] = []
    for chunk in chunks:
        blocks.append(
            "\n".join(
                [
                    f"[Chunk {chunk.get('chunk_index')} | chars {chunk.get('char_start')}-{chunk.get('char_end')} | score {chunk.get('score', 0)}]",
                    str(chunk.get("content") or chunk.get("content_preview") or ""),
                ]
            ).strip()
        )
    return "\n\n---\n\n".join(block for block in blocks if block)


def _object_stub_from_chunks(clean_key: str, settings: Settings) -> SimpleNamespace:
    public_url = (
        R2Storage(settings).public_url_for_key(clean_key) if R2Storage(settings).enabled else None
    )
    return SimpleNamespace(
        key=clean_key,
        bucket=settings.cf_r2_bucket if R2Storage(settings).enabled else None,
        size_bytes=None,
        content_type="text/plain; charset=utf-8",
        public_url=public_url,
    )


def _retrieval_metadata(
    retrieval: dict[str, object] | None, chunks: list[dict[str, object]]
) -> dict[str, object]:
    if not isinstance(retrieval, dict):
        return {
            "retrieval_source": "raw_file",
            "retrieval_mode": "raw_file",
            "vector_hits": 0,
            "sql_fallback_hits": 0,
            "chunks_used": len(chunks),
        }
    source = retrieval.get("retrieval_source") or retrieval.get("retrieval_mode") or "sql"
    return {
        "retrieval_source": source,
        "retrieval_mode": retrieval.get("retrieval_mode") or source,
        "fallback_used": bool(retrieval.get("fallback_used")),
        "vector_hits": int(retrieval.get("vector_hits") or 0),
        "sql_fallback_hits": int(retrieval.get("sql_fallback_hits") or 0),
        "candidate_count": retrieval.get("candidate_count"),
        "chunks_used": len(chunks),
    }


def _source_chunks_metadata(
    chunks: list[dict[str, object]], *, excerpt_chars: int = 360
) -> list[dict[str, object]]:
    """Return compact evidence metadata for UI citations and answer grounding."""

    source_chunks: list[dict[str, object]] = []
    for chunk in chunks:
        content = str(chunk.get("content") or chunk.get("content_preview") or "")
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        source_chunks.append(
            {
                "chunk_id": chunk.get("id"),
                "object_key": chunk.get("object_key"),
                "chunk_index": chunk.get("chunk_index"),
                "char_start": chunk.get("char_start"),
                "char_end": chunk.get("char_end"),
                "score": chunk.get("score"),
                "vector_score": chunk.get("vector_score"),
                "retrieval_source": metadata.get("retrieval_source")
                or metadata.get("source")
                or chunk.get("retrieval_source"),
                "source_archive_object_key": metadata.get("source_archive_object_key"),
                "excerpt": content[:excerpt_chars],
            }
        )
    return source_chunks


def _retrieval_summary(
    retrieval_metadata: dict[str, object] | None,
    workflow_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    if not retrieval_metadata:
        return {
            "retrieval_source": "raw_file",
            "confidence": "medium",
            "reason": "Raw bounded file excerpt was used instead of chunk retrieval.",
            "workflow_preset": workflow_metadata.get("name") if workflow_metadata else None,
        }

    vector_hits = int(retrieval_metadata.get("vector_hits") or 0)
    sql_hits = int(retrieval_metadata.get("sql_fallback_hits") or 0)
    chunks_used = int(retrieval_metadata.get("chunks_used") or 0)
    fallback_used = bool(retrieval_metadata.get("fallback_used"))
    if vector_hits > 0 and not fallback_used:
        confidence = "high"
        reason = "Vectorize returned source chunks and SQL remained available as fallback."
    elif sql_hits > 0 or chunks_used > 0:
        confidence = "medium"
        reason = "SQL chunk evidence was used; good for exact terms and logs, less semantic than Vectorize."
    else:
        confidence = "low"
        reason = "No source chunks were returned for this query."

    return {
        "workflow_preset": workflow_metadata.get("name") if workflow_metadata else None,
        "retrieval_source": retrieval_metadata.get("retrieval_source"),
        "retrieval_mode": retrieval_metadata.get("retrieval_mode"),
        "fallback_used": fallback_used,
        "vector_hits": vector_hits,
        "sql_fallback_hits": sql_hits,
        "chunks_used": chunks_used,
        "confidence": confidence,
        "reason": reason,
        "output_shape": workflow_metadata.get("output_shape") if workflow_metadata else None,
    }


def _chunk_source_metadata(
    clean_key: str,
    settings: Settings,
    retrieval: dict[str, object],
    chunks: list[dict[str, object]],
) -> dict[str, object]:
    public_url = (
        R2Storage(settings).public_url_for_key(clean_key) if R2Storage(settings).enabled else None
    )
    return {
        "object_key": clean_key,
        "storage": _storage_name(settings),
        "bucket": settings.cf_r2_bucket if R2Storage(settings).enabled else None,
        "size_bytes": None,
        "content_type": "text/plain; charset=utf-8",
        "public_url": public_url,
        "truncated": False,
        "decode_replacements": False,
        "chunked": True,
        "chunks_used": len(chunks),
        "chunk_indexes": [chunk.get("chunk_index") for chunk in chunks],
        "retrieval_query": retrieval.get("query"),
        "retrieval_candidate_count": retrieval.get("candidate_count"),
        "retrieval_source": retrieval.get("retrieval_source") or retrieval.get("retrieval_mode"),
        "vector_hits": retrieval.get("vector_hits", 0),
        "sql_fallback_hits": retrieval.get("sql_fallback_hits", 0),
    }


def _chat_with_file_model_error_response(
    *,
    stage: str,
    error_code: str,
    message: str,
    payload: dict[str, object],
    selected_model: str,
    fallback_models: list[str],
    source: dict[str, object],
    source_label: str,
    obj,
    timings: dict[str, float | None],
    sources: list[dict[str, object]] | None = None,
    source_citations: list[dict[str, object]] | None = None,
    retrieval_metadata: dict[str, object] | None = None,
    lane: str = "uploads",
) -> dict[str, object]:
    citations = source_citations or [
        {
            "label": source_label,
            "object_key": obj.key,
            "public_url": obj.public_url,
            "lane": lane,
        }
    ]
    return {
        "ok": False,
        "reply": "",
        "stage": stage,
        "error_code": error_code,
        "message": message,
        "model_used": payload.get("model") or selected_model,
        "selected_model": selected_model,
        "fallback_models": fallback_models,
        "usage": None,
        "raw_finish_reason": "timeout" if error_code == "chat_with_file_timeout" else "error",
        "completion_truncated": False,
        "empty_reply": True,
        "attempts": None,
        "timings": timings,
        "file_count": len(citations),
        "source": source,
        "sources": sources or [source],
        "retrieval_metadata": retrieval_metadata,
        "retrieval_summary": _retrieval_summary(retrieval_metadata, None),
        "source_chunks": [],
        "source_citation": citations[0],
        "source_citations": citations,
        "hint": "Retry with skip_model=true to verify file read/prompt build, or lower max_file_chars/use a faster model for smoke tests.",
    }


def _require_r2_lane(
    settings: Settings,
    lane: str,
    *,
    require_read: bool,
    require_write: bool = False,
) -> dict[str, Any]:
    lane_config = settings.r2_lane(lane)
    if lane_config is None:
        allowed = [item["lane"] for item in settings.r2_ecosystem_lanes]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": "Unknown R2 lane.", "allowed_lanes": allowed},
        )
    if not lane_config.get("bucket"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"R2 lane {lane_config['lane']!r} has no bucket configured.",
        )
    if require_read and not lane_config.get("readable"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": f"R2 lane {lane_config['lane']!r} is not readable with the current credentials.",
                "access_mode": lane_config.get("access_mode"),
                "required_env": [
                    "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY",
                    "R2_MULTI_BUCKET_READ_ENABLED=true",
                    "R2_READ_ACCESS_KEY_ID",
                    "R2_READ_SECRET_ACCESS_KEY",
                ],
            },
        )
    if require_write and not lane_config.get("writable"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": f"R2 lane {lane_config['lane']!r} is not writable with the current credentials.",
                "access_mode": lane_config.get("access_mode"),
                "required_env": [
                    "R2_MULTI_BUCKET_WRITE_ENABLED=true",
                    "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY",
                ],
            },
        )
    return lane_config


def _target_upload_lane(settings: Settings, lane: str) -> dict[str, Any]:
    lane_config = _require_r2_lane(settings, lane, require_read=False)
    if lane_config.get("primary_upload_lane") and _storage_name(settings) == "local":
        return lane_config
    return _require_r2_lane(settings, lane, require_read=False, require_write=True)


def _upload_metadata(lane_config: dict[str, Any], test_run_id: str | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {
        "lane": lane_config.get("lane"),
        "bucket": lane_config.get("bucket"),
        "access_mode": lane_config.get("access_mode"),
    }
    if test_run_id:
        metadata["test_run_id"] = test_run_id
    return metadata


def _r2_read_only_for_lane(lane_config: dict[str, Any]) -> bool:
    return not bool(lane_config.get("writable"))


def _read_object_for_lane(
    settings: Settings,
    lane_config: dict[str, Any],
    key: str,
    *,
    max_bytes: int,
):
    if lane_config.get("primary_upload_lane") and _storage_name(settings) == "local":
        return _storage(settings).read_object(key, max_bytes=max_bytes)
    return R2Storage(settings).read_object(
        key,
        max_bytes=max_bytes,
        bucket=str(lane_config["bucket"]),
        public_base_url=lane_config.get("public_base_url"),
        read_only=_r2_read_only_for_lane(lane_config),
    )


def _lane_storage_error_response(
    *,
    operation: str,
    lane_config: dict[str, Any],
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
        "storage": "r2",
        "lane": lane_config.get("lane"),
        "bucket": lane_config.get("bucket"),
        "access_mode": lane_config.get("access_mode"),
        "key_or_prefix": key_or_prefix,
    }


def _text_preview_supported(key: str, content_type: str | None) -> bool:
    suffix = Path(key).suffix.lower()
    supported_suffixes = {
        ".txt",
        ".md",
        ".log",
        ".json",
        ".jsonl",
        ".csv",
        ".tsv",
        ".html",
        ".htm",
        ".xml",
        ".rss",
        ".pdf",
        ".docx",
        ".xlsx",
        ".yaml",
        ".yml",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".sql",
        ".sh",
        ".toml",
        ".ini",
        ".cfg",
    }
    if suffix in supported_suffixes:
        return True
    media_type = (content_type or "").lower()
    return media_type.startswith("text/") or any(
        token in media_type
        for token in ["json", "xml", "csv", "pdf", "wordprocessingml", "spreadsheetml"]
    )


def _stream_r2_body(body: Any, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    try:
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Object key is required"
        )
    parts = [part for part in clean_key.split("/") if part]
    if any(part in {".", ".."} for part in parts) or "\x00" in clean_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid object key")
    if not allow_trailing_slash and clean_key.endswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Object key must point to a file"
        )
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


def _decode_base64_upload(
    content_base64: str, content_type: str | None
) -> tuple[str | None, bytes]:
    raw = content_base64.strip()
    detected_content_type = content_type
    if raw.startswith("data:") and "," in raw:
        header, raw = raw.split(",", 1)
        if ";base64" in header and not detected_content_type:
            detected_content_type = header.removeprefix("data:").split(";", 1)[0] or None
    return detected_content_type, base64.b64decode(raw, validate=True)


def _extract_object_text(
    obj, settings: Settings, *, max_chars: int | None = None
) -> dict[str, object]:
    suffix = Path(obj.key).suffix.lower()
    if suffix in {".pdf", ".docx", ".xlsx", ".csv", ".json", ".html", ".htm"}:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".bin") as tmp:
            temp_path = Path(tmp.name)
            tmp.write(obj.content)
        extracted = extract_text_with_metadata(
            temp_path,
            obj.content_type,
            max_chars=max_chars or settings.document_extract_max_chars,
            pdf_max_pages=settings.document_extract_pdf_max_pages,
            csv_max_rows=settings.document_extract_csv_max_rows,
            xlsx_max_rows_per_sheet=settings.document_extract_xlsx_max_rows_per_sheet,
            xlsx_max_sheets=settings.document_extract_xlsx_max_sheets,
            docx_max_table_rows=settings.document_extract_docx_max_table_rows,
        )
        if extracted.supported and extracted.text:
            return {
                "content": extracted.text,
                "decode_replacements": False,
                "truncated": extracted.truncated,
                "extraction": extracted.as_dict(),
            }
    content, had_decode_replacements = _decode_text(obj.content)
    if max_chars and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    else:
        truncated = False
    return {
        "content": content,
        "decode_replacements": had_decode_replacements,
        "truncated": truncated,
        "extraction": {
            "supported": suffix not in {".zip"},
            "extractor": "utf8_decode",
            "suffix": suffix,
            "char_count": len(content),
            "truncated": truncated,
        },
    }


def _decode_text(content: bytes) -> tuple[str, bool]:
    decoded = content.decode("utf-8", errors="replace")
    return decoded, "\ufffd" in decoded


def _source_metadata(
    obj,
    settings: Settings,
    *,
    truncated: bool,
    decode_replacements: bool,
    lane: str = "uploads",
) -> dict[str, object]:
    return {
        "lane": lane,
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

from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
import time
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.chunking import chunks_to_dicts, split_text_into_chunks
from app.ingestion.file_ingestion import ingest_bytes_content, ingest_text_content, ingest_upload
from app.ingestion.zip_ingestion import UnsafeZipError, inspect_zip
from app.services.brand_modes import build_system_prompt
from app.services.context_manager import ContextWindow
from app.services.model_router import Mode, ModelRouter
from app.services.openrouter import OpenRouterClient
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
    test_run_id: str | None = Field(None, max_length=120)


class Base64UploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_base64: str = Field(..., min_length=1)
    content_type: str | None = None
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
    use_chunks: bool = Field(False, description="Use persisted SQL chunks instead of injecting the raw file excerpt.")
    use_vectorize: bool = Field(False, description="When use_chunks is true, try Vectorize semantic retrieval first.")
    vectorize_fallback_sql: bool = Field(True, description="Fall back to SQL lexical chunk search if Vectorize is disabled, empty, or fails.")
    chunk_query: str | None = Field(None, max_length=2000, description="Optional retrieval query; defaults to the user message.")
    chunk_limit: int | None = Field(None, ge=1, le=30)
    auto_chunk: bool = Field(False, description="If use_chunks is true and no chunks exist, read the file and create chunks first.")
    test_run_id: str | None = Field(None, max_length=120)


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
    db_record = SqlStore(settings).record_file(result)
    return {"ok": True, "file": result.__dict__, "db_recorded": bool(db_record.get("ok")), "db_error": db_record.get("error")}


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
    db_record = SqlStore(settings).record_file(result, extra_metadata={"test_run_id": payload.test_run_id} if payload.test_run_id else None)
    return {"ok": True, "file": result.__dict__, "db_recorded": bool(db_record.get("ok")), "db_error": db_record.get("error")}


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
    db_record = SqlStore(settings).record_file(result, extra_metadata={"test_run_id": payload.test_run_id} if payload.test_run_id else None)
    return {"ok": True, "file": result.__dict__, "db_recorded": bool(db_record.get("ok")), "db_error": db_record.get("error")}


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
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    except RuntimeError as exc:
        return _storage_error_response(
            operation="chunk_read",
            settings=settings,
            key_or_prefix=clean_key,
            error=exc,
        )

    content, had_decode_replacements = _decode_text(obj.content)
    max_chars = payload.max_chars or settings.file_chunk_max_chars
    overlap_chars = payload.overlap_chars if payload.overlap_chars is not None else settings.file_chunk_overlap_chars
    max_chunks = payload.max_chunks or settings.file_chunk_max_count
    chunks = split_text_into_chunks(
        content,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        max_chunks=max_chunks,
    )
    chunk_dicts = chunks_to_dicts(chunks)
    source = _source_metadata(obj, settings, truncated=False, decode_replacements=had_decode_replacements)
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
            "total_token_estimate": sum(int(chunk.get("token_estimate") or 0) for chunk in chunk_dicts),
        },
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
        "chunks_preview": [
            {**chunk, "content": chunk["content"][:360]}
            for chunk in chunk_dicts[:5]
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
            return {"ok": False, "stage": "chunk_check", "error": existing.get("error") if isinstance(existing, dict) else "Chunk lookup failed."}
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
        return {"ok": False, "stage": "list_chunks", "error": chunks_result.get("error") if isinstance(chunks_result, dict) else "Chunk list failed."}
    if not chunks:
        return {"ok": False, "stage": "list_chunks", "error_code": "no_chunks_found", "message": "No persisted chunks found to vectorize."}

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
    """Ask a question about one stored R2/local object.

    V1 intentionally injects a bounded text excerpt directly into the model context.
    The endpoint now returns stage timings and bounded model-call diagnostics so
    hosted smoke-test runners do not get stuck behind an opaque timeout.
    """

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
    stage = "validate_key"

    try:
        clean_key = _time_stage(timings, "validate_key_seconds", lambda: _validate_object_key(request.object_key))
    except HTTPException:
        raise

    source_label = Path(clean_key).name or clean_key

    if request.use_chunks:
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
                chunk_index_result = _time_stage(
                    timings,
                    "chunk_index_seconds",
                    lambda: _chunk_file_for_chat(clean_key=clean_key, settings=settings),
                )
            except FileNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
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
        retrieval_metadata = _retrieval_metadata(retrieval, chunks)
        truncated = False
        had_decode_replacements = False
    else:
        stage = "read_file"
        try:
            obj = _time_stage(
                timings,
                "read_file_seconds",
                lambda: _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes),
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
        except RuntimeError as exc:
            response = _storage_error_response(
                operation="chat_with_file_read",
                settings=settings,
                key_or_prefix=clean_key,
                error=exc,
            )
            response["stage"] = stage
            response["timings"] = _finalise_timings(timings, total_started)
            return response

        stage = "decode_file"
        content, had_decode_replacements = _time_stage(timings, "decode_seconds", lambda: _decode_text(obj.content))
        max_chars = request.max_file_chars or settings.max_file_chat_chars
        excerpt = content[:max_chars]
        truncated = len(content) > len(excerpt)
        source = _source_metadata(obj, settings, truncated=truncated, decode_replacements=had_decode_replacements)
        retrieval_metadata = None

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
            "prompt_message_count": len(payload.get("messages", [])),
            "file_excerpt_chars": len(excerpt),
            "source": source,
            "retrieval_metadata": retrieval_metadata,
            "source_citation": {
                "label": source_label,
                "object_key": obj.key,
                "public_url": obj.public_url,
            },
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
            source_label=source_label,
            obj=obj,
            timings=_finalise_timings(timings, total_started),
            retrieval_metadata=retrieval_metadata,
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
            source_label=source_label,
            obj=obj,
            timings=_finalise_timings(timings, total_started),
            retrieval_metadata=retrieval_metadata,
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
    db_record = SqlStore(settings).record_chat(
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
            "retrieval_metadata": retrieval_metadata,
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
        "timings": _finalise_timings(timings, total_started),
        "conversation_id": db_record.get("conversation_id") or request.conversation_id,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
        "source": source,
        "retrieval_metadata": retrieval_metadata,
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
        return {"ok": False, "stage": "embeddings", "embeddings": embeddings.safe_config, "error": "Embeddings disabled or not configured."}
    if not vectorize.enabled:
        return {"ok": False, "stage": "vectorize", "vectorize": vectorize.safe_config, "error": "Vectorize disabled or not configured."}

    clean_chunks = [chunk for chunk in chunks if chunk.get("id") and (chunk.get("content") or chunk.get("content_preview"))]
    if not clean_chunks:
        return {"ok": False, "stage": "prepare", "error": "No chunk IDs/content available for vectorization."}

    requested_batch_size = batch_size or settings.embeddings_max_batch_size
    safe_batch_size = max(1, min(int(requested_batch_size), int(settings.embeddings_max_batch_size), 100))
    upserts: list[dict[str, object]] = []
    embedding_batches = 0
    vector_count = 0
    mutation_ids: list[str] = []

    for batch in _batches(clean_chunks, safe_batch_size):
        texts = [str(chunk.get("content") or chunk.get("content_preview") or "") for chunk in batch]
        embedding_result = await embeddings.embed_texts(texts)
        embedding_batches += 1
        if not embedding_result.get("ok"):
            return {"ok": False, "stage": "embedding", "batch": embedding_batches, "embedding_error": embedding_result}
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
            return {"ok": False, "stage": "upsert", "batch": embedding_batches, "vectorize_error": upsert_result}
        raw_result = upsert_result.get("result") if isinstance(upsert_result.get("result"), dict) else {}
        mutation_id = raw_result.get("mutationId") if isinstance(raw_result, dict) else None
        if mutation_id:
            mutation_ids.append(str(mutation_id))
        vector_count += len(vector_payload)
        upserts.append({"batch": embedding_batches, "count": len(vector_payload), "mutation_id": mutation_id, "status_code": upsert_result.get("status_code")})

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
            vector_diag["query"] = {k: v for k, v in query_result.items() if k not in {"raw", "matches"}}
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
        fallback = SqlStore(settings).search_file_chunks(query=query, object_key=object_key, limit=limit)
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
    return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]


def _time_stage(timings: dict[str, float | None], key: str, func):  # noqa: ANN001, ANN202
    started = time.perf_counter()
    try:
        return func()
    finally:
        timings[key] = round(time.perf_counter() - started, 3)


def _finalise_timings(timings: dict[str, float | None], total_started: float) -> dict[str, float | None]:
    final = dict(timings)
    final["total_seconds"] = round(time.perf_counter() - total_started, 3)
    return final


def _build_file_chat_payload(
    *,
    request: ChatWithFileRequest,
    settings: Settings,
    source_label: str,
    obj,
    excerpt: str,
    truncated: bool,
) -> dict[str, object]:
    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    effective_mode = router_service.resolve_mode(task, request.mode)
    selected_model = router_service.select_model(task, request.model)
    fallback_models = router_service.fallback_models_for_task(task, selected_model)

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
    return {
        "payload": payload,
        "fallback_models": fallback_models,
        "selected_model": selected_model,
        "effective_mode": effective_mode,
    }


def _chunk_file_for_chat(*, clean_key: str, settings: Settings) -> dict[str, object]:
    obj = _storage(settings).read_object(clean_key, max_bytes=settings.max_file_read_bytes)
    content, had_decode_replacements = _decode_text(obj.content)
    chunks = split_text_into_chunks(
        content,
        max_chars=settings.file_chunk_max_chars,
        overlap_chars=settings.file_chunk_overlap_chars,
        max_chunks=settings.file_chunk_max_count,
    )
    source = _source_metadata(obj, settings, truncated=False, decode_replacements=had_decode_replacements)
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
    public_url = R2Storage(settings).public_url_for_key(clean_key) if R2Storage(settings).enabled else None
    return SimpleNamespace(
        key=clean_key,
        bucket=settings.cf_r2_bucket if R2Storage(settings).enabled else None,
        size_bytes=None,
        content_type="text/plain; charset=utf-8",
        public_url=public_url,
    )


def _retrieval_metadata(retrieval: dict[str, object] | None, chunks: list[dict[str, object]]) -> dict[str, object]:
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


def _chunk_source_metadata(
    clean_key: str,
    settings: Settings,
    retrieval: dict[str, object],
    chunks: list[dict[str, object]],
) -> dict[str, object]:
    public_url = R2Storage(settings).public_url_for_key(clean_key) if R2Storage(settings).enabled else None
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
    retrieval_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
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
        "source": source,
        "retrieval_metadata": retrieval_metadata,
        "source_citation": {
            "label": source_label,
            "object_key": obj.key,
            "public_url": obj.public_url,
        },
        "hint": "Retry with skip_model=true to verify file read/prompt build, or lower max_file_chars/use a faster model for smoke tests.",
    }


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

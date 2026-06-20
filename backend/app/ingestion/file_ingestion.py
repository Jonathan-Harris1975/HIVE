from __future__ import annotations

import mimetypes
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import Settings
from app.ingestion.text_extractors import chunk_text, extract_text_with_metadata
from app.ingestion.zip_ingestion import inspect_zip
from app.storage.local_blob import LocalBlobStorage
from app.storage.r2 import R2Storage, sha256_file


@dataclass(frozen=True)
class IngestionResult:
    file_id: str
    original_name: str
    content_type: str | None
    size_bytes: int
    sha256: str
    object_key: str
    storage: str
    public_url: str | None
    extracted_text_chars: int
    chunk_count: int
    zip_member_count: int = 0
    zip_members_preview: list[dict] | None = None
    supported_for_text: bool = False
    extraction: dict | None = None


def _store_path(
    temp_path: Path,
    object_key: str,
    content_type: str | None,
    settings: Settings,
    *,
    lane_config: dict[str, Any] | None = None,
) -> tuple[object, str]:
    r2 = R2Storage(settings)
    if r2.enabled or (lane_config and r2.write_enabled):
        bucket = str(lane_config["bucket"]) if lane_config and lane_config.get("bucket") else None
        public_base_url = lane_config.get("public_base_url") if lane_config else None
        return (
            r2.put_file(
                temp_path,
                object_key,
                content_type=content_type,
                bucket=bucket,
                public_base_url=public_base_url,
            ),
            "r2",
        )
    return LocalBlobStorage().put_file(temp_path, object_key, content_type=content_type), "local"


def _safe_original_name(filename: str | None, fallback: str = "upload.txt") -> str:
    name = Path(filename or fallback).name.strip()
    return name or fallback


def _human_readable_object_key(file_id: str, original_name: str) -> str:
    """Return a readable, collision-resistant object key for user uploads."""

    now = datetime.now(timezone.utc)
    path = Path(original_name)
    stem = path.stem or "upload"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip(".-_").lower() or "upload"
    slug = slug[:80]
    return f"uploads/{now:%Y/%m/%d}/{slug}-{file_id[:8]}/{original_name}"


def ingest_text_content(
    *,
    filename: str,
    content: str,
    settings: Settings,
    content_type: str | None = "text/plain; charset=utf-8",
    lane_config: dict[str, Any] | None = None,
) -> IngestionResult:
    return ingest_bytes_content(
        filename=filename,
        data=content.encode("utf-8"),
        settings=settings,
        content_type=content_type or "text/plain; charset=utf-8",
        fallback_name="upload.txt",
        known_text=content,
        lane_config=lane_config,
    )


def ingest_bytes_content(
    *,
    filename: str,
    data: bytes,
    settings: Settings,
    content_type: str | None = None,
    fallback_name: str = "upload.bin",
    known_text: str | None = None,
    lane_config: dict[str, Any] | None = None,
) -> IngestionResult:
    """Store arbitrary uploaded bytes and run the v1-safe inspection pipeline.

    This powers phone/ReqBin-friendly base64 uploads as well as JSON text uploads.
    ZIP files are inspected, not extracted into R2 in v1; unsafe ZIPs are rejected.
    """

    if len(data) > settings.max_upload_bytes:
        raise ValueError(f"Upload exceeds max size of {settings.max_upload_bytes} bytes")

    file_id = str(uuid.uuid4())
    original_name = _safe_original_name(filename, fallback=fallback_name)
    suffix = Path(original_name).suffix.lower()
    resolved_content_type = content_type or mimetypes.guess_type(original_name)[0]
    object_key = _human_readable_object_key(file_id, original_name)

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(data)

    return _ingest_path(
        temp_path=temp_path,
        file_id=file_id,
        original_name=original_name,
        object_key=object_key,
        content_type=resolved_content_type,
        settings=settings,
        known_text=known_text,
        lane_config=lane_config,
    )


async def ingest_upload(
    upload: UploadFile,
    settings: Settings,
    *,
    lane_config: dict[str, Any] | None = None,
) -> IngestionResult:
    file_id = str(uuid.uuid4())
    original_name = _safe_original_name(upload.filename, fallback="upload.bin")
    suffix = Path(original_name).suffix.lower()
    content_type = upload.content_type or mimetypes.guess_type(original_name)[0]
    object_key = _human_readable_object_key(file_id, original_name)

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = Path(tmp.name)
        size = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise ValueError(f"Upload exceeds max size of {settings.max_upload_bytes} bytes")
            tmp.write(chunk)

    return _ingest_path(
        temp_path=temp_path,
        file_id=file_id,
        original_name=original_name,
        object_key=object_key,
        content_type=content_type,
        settings=settings,
        lane_config=lane_config,
    )


def _ingest_path(
    *,
    temp_path: Path,
    file_id: str,
    original_name: str,
    object_key: str,
    content_type: str | None,
    settings: Settings,
    known_text: str | None = None,
    lane_config: dict[str, Any] | None = None,
) -> IngestionResult:
    suffix = Path(original_name).suffix.lower()
    digest = sha256_file(temp_path)

    zip_members = []
    if suffix == ".zip":
        # Inspect before storage so unsafe archives never enter the bucket.
        zip_members = inspect_zip(
            temp_path,
            max_files=settings.max_zip_files,
            max_uncompressed_bytes=settings.max_zip_uncompressed_bytes,
        )

    stored, storage_name = _store_path(
        temp_path, object_key, content_type, settings, lane_config=lane_config
    )

    extraction_meta: dict | None = None
    if suffix == ".zip":
        extracted = ""
        extraction_meta = {"supported": False, "extractor": "zip_inspect_only", "note": "Use /v1/files/zip/extract-text to extract bounded text from archives."}
    elif known_text is not None:
        extracted = known_text[: settings.document_extract_max_chars]
        extraction_meta = {"supported": True, "extractor": "known_text", "char_count": len(extracted), "truncated": len(known_text) > len(extracted)}
    else:
        extraction = extract_text_with_metadata(
            temp_path,
            content_type,
            max_chars=settings.document_extract_max_chars,
            pdf_max_pages=settings.document_extract_pdf_max_pages,
            csv_max_rows=settings.document_extract_csv_max_rows,
            xlsx_max_rows_per_sheet=settings.document_extract_xlsx_max_rows_per_sheet,
            xlsx_max_sheets=settings.document_extract_xlsx_max_sheets,
            docx_max_table_rows=settings.document_extract_docx_max_table_rows,
        )
        extracted = extraction.text
        extraction_meta = extraction.as_dict()
    chunks = list(chunk_text(extracted)) if extracted else []

    return IngestionResult(
        file_id=file_id,
        original_name=original_name,
        content_type=content_type,
        size_bytes=stored.size_bytes,
        sha256=digest,
        object_key=object_key,
        storage=storage_name,
        public_url=stored.public_url,
        extracted_text_chars=len(extracted),
        chunk_count=len(chunks),
        zip_member_count=len(zip_members),
        zip_members_preview=[asdict(member) for member in zip_members[:100]] if zip_members else None,
        supported_for_text=bool(extracted),
        extraction=extraction_meta,
    )

from __future__ import annotations

import mimetypes
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.config import Settings
from app.ingestion.text_extractors import chunk_text, extract_text
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


def _store_path(
    temp_path: Path,
    object_key: str,
    content_type: str | None,
    settings: Settings,
) -> tuple[object, str]:
    r2 = R2Storage(settings)
    if r2.enabled:
        return r2.put_file(temp_path, object_key, content_type=content_type), "r2"
    return LocalBlobStorage().put_file(temp_path, object_key, content_type=content_type), "local"


def _safe_original_name(filename: str | None, fallback: str = "upload.txt") -> str:
    name = Path(filename or fallback).name.strip()
    return name or fallback


def ingest_text_content(
    *,
    filename: str,
    content: str,
    settings: Settings,
    content_type: str | None = "text/plain; charset=utf-8",
) -> IngestionResult:
    file_id = str(uuid.uuid4())
    original_name = _safe_original_name(filename, fallback="upload.txt")
    object_key = f"uploads/{file_id}/{original_name}"
    data = content.encode("utf-8")
    if len(data) > settings.max_upload_bytes:
        raise ValueError(f"Upload exceeds max size of {settings.max_upload_bytes} bytes")

    suffix = Path(original_name).suffix.lower() or ".txt"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = Path(tmp.name)
        tmp.write(data)

    digest = sha256_file(temp_path)
    stored, storage_name = _store_path(temp_path, object_key, content_type, settings)
    chunks = list(chunk_text(content)) if content else []

    return IngestionResult(
        file_id=file_id,
        original_name=original_name,
        content_type=content_type,
        size_bytes=stored.size_bytes,
        sha256=digest,
        object_key=object_key,
        storage=storage_name,
        public_url=stored.public_url,
        extracted_text_chars=len(content),
        chunk_count=len(chunks),
        supported_for_text=bool(content),
    )


async def ingest_upload(upload: UploadFile, settings: Settings) -> IngestionResult:
    file_id = str(uuid.uuid4())
    original_name = Path(upload.filename or "upload.bin").name
    suffix = Path(original_name).suffix.lower()
    content_type = upload.content_type or mimetypes.guess_type(original_name)[0]
    object_key = f"uploads/{file_id}/{original_name}"

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

    digest = sha256_file(temp_path)
    stored, storage_name = _store_path(temp_path, object_key, content_type, settings)

    zip_members = []
    if suffix == ".zip":
        zip_members = inspect_zip(
            temp_path,
            max_files=settings.max_zip_files,
            max_uncompressed_bytes=settings.max_zip_uncompressed_bytes,
        )

    extracted = "" if suffix == ".zip" else extract_text(temp_path, content_type)
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
    )

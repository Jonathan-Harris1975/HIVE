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
    extracted_text_chars: int
    chunk_count: int
    zip_member_count: int = 0
    zip_members_preview: list[dict] | None = None
    supported_for_text: bool = False


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
    r2 = R2Storage(settings)
    if r2.enabled:
        stored = r2.put_file(temp_path, object_key, content_type=content_type)
        storage_name = "r2"
    else:
        stored = LocalBlobStorage().put_file(temp_path, object_key, content_type=content_type)
        storage_name = "local"

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
        extracted_text_chars=len(extracted),
        chunk_count=len(chunks),
        zip_member_count=len(zip_members),
        zip_members_preview=[asdict(member) for member in zip_members[:100]] if zip_members else None,
        supported_for_text=bool(extracted),
    )

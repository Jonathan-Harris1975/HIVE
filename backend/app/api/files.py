from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.file_ingestion import ingest_text_content, ingest_upload
from app.ingestion.zip_ingestion import UnsafeZipError

router = APIRouter(tags=["files"], dependencies=[Depends(require_admin)])


class TextUploadRequest(BaseModel):
    filename: str = Field("hive-r2-smoke.txt", min_length=1, max_length=255)
    content: str = Field(..., min_length=0)
    content_type: str | None = "text/plain; charset=utf-8"


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

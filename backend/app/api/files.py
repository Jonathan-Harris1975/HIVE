from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.ingestion.file_ingestion import ingest_upload
from app.ingestion.zip_ingestion import UnsafeZipError

router = APIRouter(tags=["files"], dependencies=[Depends(require_admin)])


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

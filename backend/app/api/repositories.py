from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_manager import (
    RepositoryManagerError,
    cleanup_expired_repositories,
    cleanup_repository,
    get_repository,
    list_repositories,
    register_repository,
    reindex_repository,
    repository_diff,
)

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_admin)])


def _not_found(repository_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown repository_id: {repository_id}",
    )


@router.post("/repositories")
async def upload_repository(
    upload: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if not settings.repository_manager_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Repository Manager disabled")
    content = await upload.read()
    try:
        manifest = register_repository(
            content,
            settings=settings,
            source_filename=upload.filename or "repository.zip",
            max_files=settings.repository_max_files,
            max_uncompressed_bytes=settings.repository_max_uncompressed_bytes,
        )
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return manifest.public_payload()


@router.get("/repositories")
async def get_repositories() -> dict[str, object]:
    return {"repositories": [summary.__dict__ for summary in list_repositories()]}


@router.get("/repositories/{repository_id}")
async def get_repository_manifest(repository_id: str) -> dict[str, object]:
    record = get_repository(repository_id)
    if record is None:
        raise _not_found(repository_id)
    return record.manifest.public_payload()


@router.get("/repositories/{repository_id}/diff")
async def get_repository_diff(repository_id: str) -> dict[str, object]:
    diff = repository_diff(repository_id)
    if diff is None:
        raise _not_found(repository_id)
    return diff


@router.post("/repositories/{repository_id}/reindex")
async def post_repository_reindex(repository_id: str) -> dict[str, object]:
    try:
        manifest = reindex_repository(repository_id)
    except RepositoryManagerError as error:
        raise _not_found(repository_id) from error
    return manifest.public_payload()


@router.delete("/repositories/{repository_id}")
async def delete_repository(repository_id: str) -> dict[str, object]:
    removed = cleanup_repository(repository_id)
    if not removed:
        raise _not_found(repository_id)
    return {"repository_id": repository_id, "removed": True}


@router.post("/repositories/cleanup")
async def post_cleanup_expired(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    removed = cleanup_expired_repositories(ttl_seconds=settings.repository_ttl_seconds)
    return {"removed": removed, "removed_count": len(removed)}

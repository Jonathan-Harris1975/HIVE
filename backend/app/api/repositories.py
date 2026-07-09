from __future__ import annotations

import json

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
from app.storage.r2 import R2Storage
from app.services.repository_pipeline import run_repository_pipeline

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_admin)])

# The dedicated R2 bucket for repository manifests and metadata.
# Matches the CF R2 bucket 'hive-repositories' specified in the sprint brief.
_REPOSITORIES_BUCKET = "hive-repositories"


def _not_found(repository_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown repository_id: {repository_id}",
    )


def _persist_manifest_to_r2(manifest_payload: dict, settings: Settings) -> bool:
    """Best-effort persistence of a repository manifest to R2.

    Writes to the 'hive-repositories' bucket (CF R2) under the key
    `manifests/{repository_id}.json`. Failures are swallowed and logged
    as False so an R2 outage never breaks repository registration.
    """
    r2 = R2Storage(settings)
    if not r2.write_enabled:
        return False
    repository_id = manifest_payload.get("repository_id", "unknown")
    key = f"manifests/{repository_id}.json"
    try:
        import io
        import tempfile
        from pathlib import Path

        payload_bytes = json.dumps(manifest_payload, ensure_ascii=False, default=str).encode("utf-8")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp.write(payload_bytes)
            tmp_path = Path(tmp.name)
        try:
            r2.put_file(
                tmp_path,
                key,
                content_type="application/json",
                bucket=_REPOSITORIES_BUCKET,
                public_base_url=None,  # manifests are not publicly exposed
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 - persistence must never break registration
        return False


@router.post("/repositories")
async def upload_repository(
    upload: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    # RC1: Full pipeline — upload → extraction → fingerprint → manifest →
    # R2 persist → Repository Memory → QA → Council → Learning → AI Search.
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

    payload = manifest.public_payload()
    r2_persisted = _persist_manifest_to_r2(payload, settings)
    payload["r2_persisted"] = r2_persisted

    # Execute the downstream pipeline (non-blocking stages, graceful degradation).
    pipeline_result = await run_repository_pipeline(settings, manifest, r2_persisted=r2_persisted)
    payload["pipeline"] = pipeline_result

    return payload


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
async def post_repository_reindex(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        manifest = reindex_repository(repository_id)
    except RepositoryManagerError as error:
        raise _not_found(repository_id) from error

    payload = manifest.public_payload()
    r2_persisted = _persist_manifest_to_r2(payload, settings)
    payload["r2_persisted"] = r2_persisted
    return payload


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

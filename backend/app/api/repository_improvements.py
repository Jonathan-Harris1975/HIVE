from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_improvements import (
    RepositoryImprovementError,
    get_improvement_job,
    improvement_artifact,
    latest_improvement_job,
    start_improvement_job,
)
from app.services.repository_manager import RepositoryManagerError
from app.services.repository_memory import RepositoryMemoryUnavailableError

router = APIRouter(tags=["repository-improvements"], dependencies=[Depends(require_admin)])


def _improvement_error_status(error: RepositoryImprovementError) -> int:
    message = str(error).lower()
    if any(
        marker in message
        for marker in (
            "already running",
            "stale for the current snapshot",
            "run repository intelligence first",
            "no repository intelligence report",
            "no actionable findings",
            "artifact is not ready",
        )
    ):
        return status.HTTP_409_CONFLICT
    if "unknown improvement artifact kind" in message:
        return status.HTTP_400_BAD_REQUEST
    if "unknown repository improvement job" in message:
        return status.HTTP_404_NOT_FOUND
    return status.HTTP_503_SERVICE_UNAVAILABLE


@router.post("/repositories/{repository_id}/improvements/run", status_code=status.HTTP_202_ACCEPTED)
async def post_repository_improvements(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Queue an isolated, LLM-assisted repository improvement job."""
    try:
        return start_improvement_job(settings, repository_id)
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RepositoryMemoryUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except RepositoryImprovementError as error:
        raise HTTPException(status_code=_improvement_error_status(error), detail=str(error)) from error


@router.get("/repositories/{repository_id}/improvements/latest")
async def get_latest_repository_improvement(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    job = latest_improvement_job(settings, repository_id)
    return {"repository_id": repository_id, "job": job}


@router.get("/repositories/{repository_id}/improvements/jobs/{job_id}")
async def get_repository_improvement(
    repository_id: str,
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    job = get_improvement_job(settings, repository_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown repository improvement job")
    return job


@router.get("/repositories/{repository_id}/improvements/jobs/{job_id}/download/{kind}")
async def download_repository_improvement(
    repository_id: str,
    job_id: str,
    kind: str,
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        filename, content, content_type = improvement_artifact(settings, repository_id, job_id, kind)
    except RepositoryImprovementError as error:
        raise HTTPException(status_code=_improvement_error_status(error), detail=str(error)) from error
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )

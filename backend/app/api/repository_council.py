from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_council import (
    run_and_record_council,
    get_council_history as get_council_history_service,
)
from app.services.repository_manager import RepositoryManagerError

router = APIRouter(tags=["repository-council"], dependencies=[Depends(require_admin)])


@router.post("/repositories/{repository_id}/council")
async def post_run_council_review(
    repository_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    try:
        report = run_and_record_council(settings, repository_id)
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return report.public_payload()


@router.get("/repositories/{repository_id}/council/history")
async def get_council_review_history(
    repository_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"repository_id": repository_id, "runs": get_council_history_service(settings, repository_id)}

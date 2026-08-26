from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_intelligence import run_repository_intelligence
from app.services.repository_manager import RepositoryManagerError
from app.services.repository_memory import RepositoryMemoryUnavailableError

router = APIRouter(tags=["repository-intelligence"], dependencies=[Depends(require_admin)])


@router.post("/repositories/{repository_id}/intelligence/run")
async def post_run_repository_intelligence(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Run the canonical combined Repository QA + Council intelligence review."""
    try:
        return run_repository_intelligence(settings, repository_id)
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RepositoryMemoryUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.security import require_admin
from app.services.repo_hygiene import repo_hygiene_report

router = APIRouter(tags=["system"], dependencies=[Depends(require_admin)])


@router.get("/system/repo-hygiene")
def repo_hygiene(
    include_hashes: bool = Query(True),
    max_files: int = Query(5000, ge=100, le=20000),
) -> dict[str, object]:
    """Return duplicate/orphan file candidates for review.

    This endpoint is intentionally read-only. It is useful for HIVE release
    hygiene checks and MAST reporting, but v1.13 never deletes repo files.
    """

    return repo_hygiene_report(include_hashes=include_hashes, max_files=max_files)

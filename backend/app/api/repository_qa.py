from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_manager import RepositoryManagerError
from app.services.repository_memory import append_history_entry
from app.services.repository_qa import run_repository_qa
from app.storage.d1 import D1MetadataStore

router = APIRouter(tags=["repository-qa"], dependencies=[Depends(require_admin)])


@router.post("/repositories/{repository_id}/qa")
async def post_run_qa(repository_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    try:
        report = run_repository_qa(repository_id)
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    payload = report.public_payload()
    store = D1MetadataStore(settings)
    append_history_entry(
        store,
        repository_id=repository_id,
        field_name="qa_history",
        entry={**payload, "occurred_at": datetime.now(UTC).isoformat()},
    )
    return payload

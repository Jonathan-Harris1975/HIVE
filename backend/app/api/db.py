from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.storage.d1 import D1MetadataStore
from app.storage.sql_store import SqlStore

router = APIRouter(tags=["database"], dependencies=[Depends(require_admin)])


class EcosystemMetadataRequest(BaseModel):
    id: str | None = None
    lane: str = Field(..., min_length=1, max_length=120)
    source_type: str = Field(..., min_length=1, max_length=120)
    source_id: str | None = Field(None, max_length=512)
    title: str | None = Field(None, max_length=512)
    url: str | None = Field(None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/db/diagnostics")
def database_diagnostics(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return safe SQL/D1 diagnostics without exposing secrets."""

    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    return {
        "ok": True,
        "sql": sql.diagnostics(),
        "d1": d1.diagnostics(),
        "recommended_split": {
            "sql": [
                "conversations",
                "messages",
                "file metadata",
                "upload records",
                "token/cost tracking",
            ],
            "d1": [
                "ecosystem metadata",
                "audit run index",
                "council report index",
                "podcast episode index",
                "ebook catalogue cache",
                "social performance snapshots",
            ],
        },
    }


@router.post("/db/init")
def init_database(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Create optional SQL and D1 schemas.

    Safe to run multiple times. Routes using HIVE chat/files do not require this
    endpoint to have run unless DATABASE_ENABLED/D1_ENABLED are being actively used.
    """

    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    sql_result = sql.init_schema()
    d1_result = d1.init_schema()
    return {
        "ok": bool(sql_result.get("ok") or not sql.enabled) and bool(d1_result.get("ok") or not d1.enabled),
        "sql": sql_result,
        "d1": d1_result,
    }


@router.post("/db/ecosystem-metadata")
def upsert_ecosystem_metadata(
    payload: EcosystemMetadataRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Store one ecosystem metadata record in D1.

    This is the v1 D1 smoke path for audit/council/podcast/ebook/social indexes.
    """

    item_id = payload.id or str(uuid.uuid4())
    result = D1MetadataStore(settings).upsert_metadata(
        item_id=item_id,
        lane=payload.lane,
        source_type=payload.source_type,
        source_id=payload.source_id,
        title=payload.title,
        url=payload.url,
        metadata=payload.metadata,
    )
    return {"ok": bool(result.get("ok")), "id": item_id, "d1": result}

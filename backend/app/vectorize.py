from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.version import BUILD_STAGE
from app.core.security import require_admin
from app.services.embeddings import CloudflareEmbeddingsClient
from app.storage.sql_store import SqlStore
from app.storage.vectorize import VectorizeClient

router = APIRouter(tags=["vectorize"], dependencies=[Depends(require_admin)])


@router.get("/vectorize/diagnostics")
async def vectorize_diagnostics(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return safe Vectorize + embeddings diagnostics without exposing tokens."""

    vectorize = VectorizeClient(settings)
    embeddings = CloudflareEmbeddingsClient(settings)
    sql = SqlStore(settings)
    vectorize_probe = await vectorize.diagnostics()
    embeddings_probe = await embeddings.diagnostics()
    return {
        "ok": bool(vectorize_probe.get("ok")) if settings.vectorize_enabled else True,
        "build": BUILD_STAGE,
        "vectorize": vectorize_probe,
        "embeddings": embeddings_probe,
        "sql_chunks_source_of_truth": True,
        "sql_enabled": sql.enabled,
        "note": "Vectorize is optional. SQL chunk search remains the fallback path.",
    }

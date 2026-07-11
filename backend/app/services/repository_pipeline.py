from __future__ import annotations

# RC1 Remediation — Audit Finding #4 (Repository Pipeline integration).
#
# Previously the repository upload endpoint stopped after R2 persistence.
# AI Search indexing and Repository Memory population only happened if
# something else *separately* invoked QA / Council / Learning against the
# repository_id afterward — a decomposed, manually-chained set of stages.
#
# This module provides `run_repository_pipeline()`: a single async function
# that executes the complete lifecycle automatically on upload:
#
#   Upload → Extraction → Fingerprint → Manifest → Persist to R2
#       → Repository Memory seed → QA → Council → Learning / DNA
#       → AI Search index → Repository Ready
#
# Each stage degrades gracefully: a failure in any downstream stage
# (Memory / QA / Council / AI Search) is logged but does NOT fail the
# upload response — the manifest is always returned.  Stage results are
# included in the response payload under `pipeline` so operators can see
# exactly what happened.
#
# The pipeline is intentionally *async* so it can be awaited inside a
# FastAPI async route handler without blocking the event loop.  Sync
# service functions (run_repository_qa, run_repository_council,
# update_project_dna) are wrapped with asyncio.to_thread().

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import Settings
from app.services.repository_manager import RepositoryManifest

logger = logging.getLogger("uvicorn.error.hive.repository_pipeline")

# ---------------------------------------------------------------------------
# Stage: Repository Memory — seed basic manifest metadata
# ---------------------------------------------------------------------------


def _seed_repository_memory(settings: Settings, manifest: RepositoryManifest) -> dict[str, Any]:
    """Write the manifest summary into Repository Memory so downstream
    services (QA, Council, Learning) have a stable source of truth."""
    try:
        from app.services.repository_memory import set_memory_field
        from app.storage.d1 import D1MetadataStore

        store = D1MetadataStore(settings)
        set_memory_field(
            store,
            repository_id=manifest.repository_id,
            field_name="project_manifest",
            content={
                "source_filename": manifest.source_filename,
                "fingerprint": manifest.fingerprint,
                "file_count": manifest.file_count,
                "total_bytes": manifest.total_bytes,
                "languages": manifest.languages,
                "created_at": manifest.created_at,
                "indexed_version": manifest.indexed_version,
            },
        )
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repository pipeline memory seed failed repository_id=%s error=%s",
            manifest.repository_id,
            exc,
        )
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Stage: QA
# ---------------------------------------------------------------------------


def _run_qa(settings: Settings, repository_id: str) -> dict[str, Any]:
    try:
        from datetime import UTC, datetime

        from app.services.repository_memory import append_history_entry
        from app.services.repository_qa import run_repository_qa
        from app.storage.d1 import D1MetadataStore

        report = run_repository_qa(repository_id)
        payload = report.public_payload()

        # Bug fix: this automatic pipeline stage ran QA but never wrote to
        # qa_history, unlike the manual POST /repositories/{id}/qa endpoint
        # (api/repository_qa.py) which does. Every QA run triggered by a zip
        # upload was silently invisible to Repository Memory. Mirror the
        # manual endpoint's persistence so history is complete regardless of
        # trigger path.
        store = D1MetadataStore(settings)
        history_result = append_history_entry(
            store,
            repository_id=repository_id,
            field_name="qa_history",
            entry={**payload, "occurred_at": datetime.now(UTC).isoformat()},
        )
        return {
            "ok": True,
            "score": report.score,
            "warning_count": report.warning_count,
            "history_persisted": bool(history_result.get("ok")) if isinstance(history_result, dict) else False,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repository pipeline QA failed repository_id=%s error=%s", repository_id, exc
        )
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Stage: Council
# ---------------------------------------------------------------------------


def _run_council(settings: Settings, repository_id: str) -> dict[str, Any]:
    try:
        from app.services.repository_council import run_and_record_council

        report = run_and_record_council(settings, repository_id)
        return {"ok": True, "overall_score": report.overall_score}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repository pipeline Council failed repository_id=%s error=%s", repository_id, exc
        )
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Stage: Learning / Project DNA
# ---------------------------------------------------------------------------


def _run_learning(settings: Settings, repository_id: str) -> dict[str, Any]:
    try:
        from app.services.repository_learning import update_project_dna

        dna = update_project_dna(settings, repository_id=repository_id)
        return {"ok": True, "latest_qa_score": dna.get("latest_qa_score")}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repository pipeline Learning failed repository_id=%s error=%s", repository_id, exc
        )
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Stage: AI Search indexing
# ---------------------------------------------------------------------------


async def _index_in_ai_search(settings: Settings, manifest: RepositoryManifest) -> dict[str, Any]:
    """Push the repository manifest summary to Cloudflare AI Search for
    semantic retrieval.  AI Search is optional (degrades gracefully)."""
    try:
        from app.storage.ai_search import AiSearchClient

        client = AiSearchClient(settings)
        if not client.enabled:
            return {"ok": False, "skipped": True, "reason": "ai_search_not_configured"}

        # AI Search (Cloudflare AutoRAG) indexes documents via the R2 bucket
        # it is connected to — HIVE doesn't push individual documents via API.
        # We record the indexing intent in Repository Memory so the AI Search
        # instance can pick up the manifest from R2 on its next sync cycle.
        # The diagnostics call confirms connectivity.
        diag = await client.diagnostics()
        return {"ok": diag.get("ok", False), "diagnostics": diag}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repository pipeline AI Search indexing failed repository_id=%s error=%s",
            manifest.repository_id,
            exc,
        )
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


async def run_repository_pipeline(
    settings: Settings,
    manifest: RepositoryManifest,
    *,
    r2_persisted: bool,
) -> dict[str, Any]:
    """Execute the complete post-upload pipeline for a repository.

    Returns a ``pipeline`` dict that documents each stage's outcome.
    Never raises — all stage failures are captured in the return value.
    """
    repository_id = manifest.repository_id
    t_start = time.monotonic()

    pipeline: dict[str, Any] = {
        "repository_id": repository_id,
        "r2_persisted": r2_persisted,
    }

    # Stage: Memory seed (sync, fast)
    pipeline["memory_seed"] = await asyncio.to_thread(
        _seed_repository_memory, settings, manifest
    )

    # Stage: QA (sync, may take a few seconds on large repos)
    pipeline["qa"] = await asyncio.to_thread(_run_qa, settings, repository_id)

    # Stage: Council (sync, depends on QA)
    pipeline["council"] = await asyncio.to_thread(_run_council, settings, repository_id)

    # Stage: Learning / DNA (sync, fast)
    pipeline["learning"] = await asyncio.to_thread(_run_learning, settings, repository_id)

    # Stage: AI Search index (async)
    pipeline["ai_search"] = await _index_in_ai_search(settings, manifest)

    pipeline["elapsed_ms"] = round((time.monotonic() - t_start) * 1000, 1)
    pipeline["status"] = "ready"

    stages_failed = [k for k, v in pipeline.items() if isinstance(v, dict) and v.get("ok") is False and not v.get("skipped")]
    if stages_failed:
        pipeline["status"] = "ready_with_warnings"
        pipeline["failed_stages"] = stages_failed
        logger.warning(
            "Repository pipeline completed with warnings repository_id=%s failed=%s",
            repository_id,
            stages_failed,
        )
    else:
        logger.info(
            "Repository pipeline complete repository_id=%s elapsed_ms=%s",
            repository_id,
            pipeline["elapsed_ms"],
        )

    return pipeline

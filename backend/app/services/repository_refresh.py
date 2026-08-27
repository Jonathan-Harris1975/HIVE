from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, cast

import httpx

from app.core.config import Settings
from app.storage.d1 import D1MetadataStore

_REFRESH_LANE = "repository_refresh"
_REFRESH_SOURCE_TYPE = "repository_refresh_job"
_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_JOB_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
_MAX_JOBS = 20
_REQUIRED_GOVERNED_REPOSITORIES = frozenset({
    "HIVE",
    "HIVE-UI",
    "AIMS",
    "AIMS-UI",
    "RAMS",
    "MAST",
    "IRS",
    "Website",
})

IngestCallback = Callable[[bytes, str, str], Awaitable[dict[str, Any]]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def github_sources(settings: Settings) -> dict[str, str]:
    """Return the configured governed repository -> GitHub slug mapping."""
    try:
        parsed = json.loads(settings.repository_github_sources_json or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("REPOSITORY_GITHUB_SOURCES_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("REPOSITORY_GITHUB_SOURCES_JSON must be a non-empty object")

    result: dict[str, str] = {}
    for raw_id, raw_slug in parsed.items():
        repository_id = str(raw_id or "").strip()
        slug = str(raw_slug or "").strip()
        if not repository_id or not _SOURCE_RE.fullmatch(slug):
            raise ValueError(
                "REPOSITORY_GITHUB_SOURCES_JSON entries must map non-empty repository ids "
                "to owner/repository GitHub slugs"
            )
        result[repository_id] = slug
    return result


def refresh_configuration(settings: Settings) -> dict[str, object]:
    try:
        sources = github_sources(settings)
        source_error = None
    except ValueError as exc:
        sources = {}
        source_error = str(exc)
    token_configured = bool(settings.github_token.strip())
    enabled = bool(settings.repository_github_refresh_enabled)
    repository_ids = set(sources)
    missing_repository_ids = sorted(_REQUIRED_GOVERNED_REPOSITORIES - repository_ids)
    unexpected_repository_ids = sorted(repository_ids - _REQUIRED_GOVERNED_REPOSITORIES)
    complete_catalogue = not missing_repository_ids and not unexpected_repository_ids
    configured = bool(
        enabled
        and sources
        and complete_catalogue
        and token_configured
        and settings.repository_github_branch.strip()
    )
    return {
        "enabled": enabled,
        "configured": configured,
        "repository_count": len(sources),
        "expected_repository_count": len(_REQUIRED_GOVERNED_REPOSITORIES),
        "repository_ids": sorted(sources),
        "complete_catalogue": complete_catalogue,
        "missing_repository_ids": missing_repository_ids,
        "unexpected_repository_ids": unexpected_repository_ids,
        "branch": settings.repository_github_branch.strip() or "main",
        "github_token_configured": token_configured,
        "trigger": "MAST post-audit job aims-audit-pipeline +15 minutes",
        "source_error": source_error,
    }


async def download_github_archive(settings: Settings, github_slug: str) -> bytes:
    """Download one GitHub branch archive with bounded size and timeout."""
    if not _SOURCE_RE.fullmatch(github_slug):
        raise ValueError(f"Invalid GitHub repository slug: {github_slug}")
    branch = settings.repository_github_branch.strip() or "main"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "HIVE-Repository-Refresh/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{github_slug}/zipball/{branch}"
    limit = max(1, int(settings.max_upload_bytes))
    timeout = float(settings.repository_github_download_timeout_seconds)
    content = bytearray()
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError(
                        f"GitHub archive for {github_slug} exceeds MAX_UPLOAD_BYTES ({limit} bytes)"
                    )
    if not content:
        raise ValueError(f"GitHub returned an empty archive for {github_slug}")
    return bytes(content)


def _persist_job(settings: Settings, payload: dict[str, Any]) -> None:
    store = D1MetadataStore(settings)
    if not store.enabled:
        return
    try:
        store.upsert_metadata(
            item_id=f"repository-refresh:{payload['job_id']}",
            lane=_REFRESH_LANE,
            source_type=_REFRESH_SOURCE_TYPE,
            source_id=str(payload["job_id"]),
            title="Monthly governed repository refresh",
            url=None,
            metadata=payload,
        )
    except Exception:
        # The refresh job still runs if observability persistence has a transient
        # fault; the API's in-process state remains authoritative for this run.
        return


def _set_job(settings: Settings, job_id: str, **changes: Any) -> dict[str, Any]:
    with _JOB_LOCK:
        job = dict(_JOBS.get(job_id) or {"job_id": job_id})
        job.update(changes)
        job["updated_at"] = _now_iso()
        _JOBS[job_id] = job
        # Keep memory bounded on long-running services.
        while len(_JOBS) > _MAX_JOBS:
            oldest = min(_JOBS.values(), key=lambda item: str(item.get("created_at") or ""))
            _JOBS.pop(str(oldest.get("job_id")), None)
        snapshot = dict(job)
    _persist_job(settings, snapshot)
    return snapshot


def _stored_job(settings: Settings, job_id: str) -> dict[str, Any] | None:
    store = D1MetadataStore(settings)
    if not store.enabled:
        return None
    try:
        result = store.list_metadata(lane=_REFRESH_LANE, limit=_MAX_JOBS)
    except Exception:
        return None
    raw_items = result.get("items")
    if not result.get("ok") or not isinstance(raw_items, list):
        return None
    for raw_row in raw_items:
        if not isinstance(raw_row, dict) or str(raw_row.get("source_id") or "") != job_id:
            continue
        raw_metadata = raw_row.get("metadata")
        if isinstance(raw_metadata, dict):
            return cast(dict[str, Any], raw_metadata)
    return None


def get_refresh_job(settings: Settings, job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        local = _JOBS.get(job_id)
        task = _TASKS.get(job_id)
        if local is not None:
            payload = dict(local)
            # A process can lose a background task only across a restart or an
            # unexpected task cancellation. Do not leave MAST polling "running"
            # forever if that happens.
            if payload.get("status") == "running" and task is not None and task.done():
                payload = _set_job(
                    settings,
                    job_id,
                    status="failed",
                    finished_at=_now_iso(),
                    error="Repository refresh worker stopped before terminal completion.",
                )
            return payload

    stored = _stored_job(settings, job_id)
    if stored is None:
        return None
    if stored.get("status") in {"accepted", "running"}:
        changes = dict(stored)
        changes.pop("job_id", None)
        changes.update(
            status="failed",
            finished_at=_now_iso(),
            error="HIVE restarted while this repository refresh job was active; rerun the refresh.",
        )
        stored = _set_job(settings, job_id, **changes)
    return stored


def active_refresh_job() -> str | None:
    with _JOB_LOCK:
        for job_id, payload in _JOBS.items():
            if payload.get("status") in {"accepted", "running"}:
                task = _TASKS.get(job_id)
                if task is None or not task.done():
                    return job_id
    return None


async def _run_refresh_job(
    settings: Settings,
    job_id: str,
    sources: dict[str, str],
    ingest: IngestCallback,
) -> None:
    _set_job(settings, job_id, status="running", started_at=_now_iso())
    results: list[dict[str, Any]] = []
    try:
        for repository_id, slug in sources.items():
            started = _now_iso()
            try:
                content = await download_github_archive(settings, slug)
                payload = await ingest(content, f"{repository_id}-main.zip", repository_id)
                raw_pipeline = payload.get("pipeline")
                pipeline: dict[str, Any] = (
                    cast(dict[str, Any], raw_pipeline) if isinstance(raw_pipeline, dict) else {}
                )
                raw_intelligence = pipeline.get("intelligence")
                intelligence: dict[str, Any] = (
                    cast(dict[str, Any], raw_intelligence)
                    if isinstance(raw_intelligence, dict)
                    else {}
                )
                ok = bool(pipeline.get("required_stages_ready") is True)
                results.append(
                    {
                        "repository_id": repository_id,
                        "github_repository": slug,
                        "ok": ok,
                        "started_at": started,
                        "finished_at": _now_iso(),
                        "pipeline_status": pipeline.get("status"),
                        "finding_count": intelligence.get("finding_count"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        "repository_id": repository_id,
                        "github_repository": slug,
                        "ok": False,
                        "started_at": started,
                        "finished_at": _now_iso(),
                        "error": str(exc),
                    }
                )
            _set_job(settings, job_id, results=list(results), completed_count=len(results))

        failures = [item for item in results if not item.get("ok")]
        _set_job(
            settings,
            job_id,
            status="completed" if not failures else "completed-with-failures",
            finished_at=_now_iso(),
            results=results,
            completed_count=len(results),
            failed_count=len(failures),
            ok=not failures,
        )
    except Exception as exc:  # noqa: BLE001
        _set_job(
            settings,
            job_id,
            status="failed",
            finished_at=_now_iso(),
            results=results,
            completed_count=len(results),
            failed_count=max(1, len(sources) - len(results)),
            ok=False,
            error=str(exc),
        )
    finally:
        with _JOB_LOCK:
            _TASKS.pop(job_id, None)


def start_refresh_job(settings: Settings, ingest: IngestCallback) -> dict[str, Any]:
    configuration = refresh_configuration(settings)
    if not settings.repository_github_refresh_enabled:
        raise RuntimeError("Monthly repository refresh is disabled")
    if not configuration.get("configured"):
        missing = configuration.get("missing_repository_ids") or []
        unexpected = configuration.get("unexpected_repository_ids") or []
        if missing or unexpected:
            raise RuntimeError(
                "Monthly repository refresh catalogue is incomplete: "
                f"missing={missing}, unexpected={unexpected}"
            )
        if not settings.github_token.strip():
            raise RuntimeError("GITHUB_TOKEN is required for governed repository refresh")
        raise RuntimeError("Monthly repository refresh configuration is incomplete")
    sources = github_sources(settings)
    if active_refresh_job() is not None:
        raise RuntimeError("A governed repository refresh is already running")

    job_id = uuid.uuid4().hex
    payload = _set_job(
        settings,
        job_id,
        created_at=_now_iso(),
        status="accepted",
        repository_count=len(sources),
        completed_count=0,
        failed_count=0,
        results=[],
        ok=None,
    )
    task = asyncio.create_task(_run_refresh_job(settings, job_id, sources, ingest))
    with _JOB_LOCK:
        _TASKS[job_id] = task
    return payload

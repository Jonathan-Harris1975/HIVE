from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_manager import (
    RepositoryManagerError,
    RepositoryWorkdirUnavailableError,
    cleanup_expired_repositories,
    cleanup_repository,
    get_repository,
    is_rehydrated,
    list_repositories,
    register_repository,
    reindex_repository,
    repository_diff,
)
from app.services.repository_memory import ALL_FIELDS, LANE, SCALAR_FIELDS, repository_memory_item_id
from app.storage.r2 import R2Storage
from app.storage.d1 import D1MetadataStore
from app.services.repository_pipeline import run_repository_pipeline
from app.services.repository_refresh import (
    get_refresh_job,
    refresh_configuration,
    start_refresh_job,
)

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_admin)])


def _not_found(repository_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Unknown repository_id: {repository_id}",
    )


def _workdir_unavailable(error: RepositoryWorkdirUnavailableError) -> HTTPException:
    # 409 Conflict: the repository exists (manifest metadata is present) but its
    # local working copy does not, so the request cannot be satisfied until the
    # operator re-uploads it. Distinct from 404 so the UI can tell "never
    # existed" apart from "exists, but needs a re-upload".
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _persist_manifest_to_r2(manifest_payload: dict, settings: Settings) -> bool:
    """Persist a repository manifest to the governed repositories R2 bucket."""
    r2 = R2Storage(settings)
    if not r2.write_enabled:
        return False
    repository_id = manifest_payload.get("repository_id", "unknown")
    key = f"manifests/{repository_id}.json"
    try:
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
                bucket=settings.r2_bucket_repositories,
                public_base_url=None,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 - caller decides whether durability is mandatory
        return False


def _persist_snapshot_to_r2(
    content: bytes,
    repository_id: str,
    settings: Settings,
) -> bool:
    """Persist the source ZIP used by QA/Council so restarts are recoverable."""
    r2 = R2Storage(settings)
    if not r2.write_enabled:
        return False
    try:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            r2.put_file(
                tmp_path,
                f"snapshots/{repository_id}.zip",
                content_type="application/zip",
                bucket=settings.r2_bucket_repositories,
                public_base_url=None,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
        return True
    except Exception:  # noqa: BLE001 - caller decides whether durability is mandatory
        return False


def _metadata_rows(result: dict[str, object]) -> list[dict[str, Any]]:
    """Return D1 metadata rows with a concrete type after runtime validation."""
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return []
    return [cast(dict[str, Any], row) for row in raw_items if isinstance(row, dict)]


def _repository_memory_readiness(
    settings: Settings,
    repository_ids: list[str],
) -> dict[str, dict[str, object]]:
    """Return fail-visible Memory/Intelligence readiness for repository cards."""
    readiness = {
        repository_id: {
            "memory_status": "unavailable",
            "profile_ready": False,
            "intelligence_ready": False,
            "memory_ready": False,
            "memory_populated_fields": [],
        }
        for repository_id in repository_ids
    }
    if not repository_ids:
        return readiness

    store = D1MetadataStore(settings)
    if not store.enabled:
        return readiness
    try:
        result = store.list_metadata(lane=LANE, limit=500)
    except Exception:  # noqa: BLE001 - readiness must not hide repository listing
        return readiness
    if not result.get("ok"):
        return readiness

    values_by_repository: dict[str, dict[str, object]] = {repository_id: {} for repository_id in repository_ids}
    for row in _metadata_rows(result):
        source_id = str(row.get("source_id") or "")
        source_type = str(row.get("source_type") or "")
        if source_id not in values_by_repository or source_type not in ALL_FIELDS:
            continue
        raw_metadata = row.get("metadata")
        metadata = cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
        values_by_repository[source_id][source_type] = metadata.get("value")

    for repository_id, values in values_by_repository.items():
        populated = sorted(
            field_name
            for field_name, value in values.items()
            if value is not None and value != "" and value != [] and value != {}
        )
        profile_ready = all(field_name in populated for field_name in SCALAR_FIELDS)
        qa_history = values.get("qa_history")
        council_history = values.get("repository_council_history")
        intelligence_history = values.get("repository_intelligence_history")
        record = get_repository(repository_id)
        current_fingerprint = record.manifest.fingerprint if record is not None else ""

        latest_qa = next(
            (entry for entry in reversed(qa_history) if isinstance(entry, dict)),
            None,
        ) if isinstance(qa_history, list) else None
        latest_council = next(
            (entry for entry in reversed(council_history) if isinstance(entry, dict)),
            None,
        ) if isinstance(council_history, list) else None
        latest_intelligence = next(
            (entry for entry in reversed(intelligence_history) if isinstance(entry, dict)),
            None,
        ) if isinstance(intelligence_history, list) else None
        intelligence_context: dict[str, object] = {}
        if isinstance(latest_intelligence, dict):
            raw_context = latest_intelligence.get("repository_context")
            if isinstance(raw_context, dict):
                intelligence_context = {str(key): value for key, value in raw_context.items()}
        intelligence_ready = bool(
            isinstance(latest_qa, dict)
            and latest_qa.get("repository_id") == repository_id
            and isinstance(latest_council, dict)
            and latest_council.get("repository_id") == repository_id
            and isinstance(latest_intelligence, dict)
            and latest_intelligence.get("repository_id") == repository_id
            and intelligence_context.get("repository_id") == repository_id
            and current_fingerprint
            and intelligence_context.get("fingerprint") == current_fingerprint
        )
        readiness[repository_id] = {
            "memory_status": "ready" if profile_ready else ("partial" if populated else "empty"),
            "profile_ready": profile_ready,
            "intelligence_ready": intelligence_ready,
            "memory_ready": profile_ready and intelligence_ready,
            "memory_populated_fields": populated,
        }
    return readiness


def _delete_repository_artifacts(repository_id: str, settings: Settings) -> dict[str, object]:
    """Delete durable repository state from R2 and Repository Memory."""
    result: dict[str, object] = {"r2_deleted": False, "memory_deleted": False}
    r2 = R2Storage(settings)
    if r2.write_enabled:
        try:
            deletion = r2.delete_objects(
                [f"manifests/{repository_id}.json", f"snapshots/{repository_id}.zip"],
                bucket=settings.r2_bucket_repositories,
            )
            result["r2_deleted"] = bool(deletion.get("ok"))
        except Exception as exc:  # noqa: BLE001
            result["r2_error"] = str(exc)

    store = D1MetadataStore(settings)
    if store.enabled:
        memory_result = store.delete_metadata_ids(
            [repository_memory_item_id(repository_id, field) for field in ALL_FIELDS]
        )
        result["memory_deleted"] = bool(memory_result.get("ok"))
        if not memory_result.get("ok"):
            result["memory_error"] = memory_result.get("message") or memory_result.get("failed")
    return result


async def _ingest_repository_content(
    content: bytes,
    source_filename: str,
    settings: Settings,
    *,
    expected_repository_id: str | None = None,
) -> dict[str, Any]:
    """Register, durably persist and fully analyse one repository archive."""
    try:
        manifest = register_repository(
            content,
            settings=settings,
            source_filename=source_filename,
            max_files=settings.repository_max_files,
            max_uncompressed_bytes=settings.repository_max_uncompressed_bytes,
        )
    except RepositoryManagerError:
        raise

    if expected_repository_id and manifest.repository_id != expected_repository_id:
        cleanup_repository(manifest.repository_id)
        raise RepositoryManagerError(
            f"GitHub source identity mismatch: expected {expected_repository_id}, "
            f"archive resolved to {manifest.repository_id}"
        )

    payload: dict[str, Any] = manifest.public_payload()
    snapshot_persisted = _persist_snapshot_to_r2(content, manifest.repository_id, settings)
    r2_persisted = _persist_manifest_to_r2(payload, settings)
    payload["r2_persisted"] = r2_persisted
    payload["snapshot_persisted"] = snapshot_persisted

    if settings.production_require_r2 and (not r2_persisted or not snapshot_persisted):
        cleanup_repository(manifest.repository_id)
        _delete_repository_artifacts(manifest.repository_id, settings)
        raise RuntimeError(
            "Repository archive was rejected because its durable R2 manifest/snapshot could not be stored. "
            "No temporary-only repository was accepted."
        )

    payload["pipeline"] = await run_repository_pipeline(
        settings, manifest, r2_persisted=r2_persisted
    )
    return payload


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
    upload_limit = int(getattr(settings, "max_upload_bytes", 100 * 1024 * 1024))
    if len(content) > upload_limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Repository upload exceeds MAX_UPLOAD_BYTES ({upload_limit} bytes)",
        )
    try:
        return await _ingest_repository_content(
            content,
            upload.filename or "repository.zip",
            settings,
        )
    except RepositoryManagerError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.post("/repositories/{repository_id}/setup")
async def post_repository_setup(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Re-run the governed Memory/QA/Council/Learning setup for an existing snapshot.

    This is the recovery path for transient D1, AI Search, or downstream setup
    failures.  It deliberately requires a real local/restored source snapshot;
    legacy manifest-only registrations must be uploaded once before repair can
    run.
    """
    record = get_repository(repository_id)
    if record is None:
        raise _not_found(repository_id)
    if is_rehydrated(record):
        raise _workdir_unavailable(
            RepositoryWorkdirUnavailableError(
                f"Repository {repository_id} has no restorable source snapshot. "
                "Re-upload it once before running setup."
            )
        )

    manifest_payload = record.manifest.public_payload()
    r2_persisted = _persist_manifest_to_r2(manifest_payload, settings)
    if settings.production_require_r2 and not r2_persisted:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Repository setup could not refresh its durable R2 manifest.",
        )

    pipeline = await run_repository_pipeline(
        settings,
        record.manifest,
        r2_persisted=r2_persisted,
    )
    return {
        "repository_id": repository_id,
        "pipeline": pipeline,
        "ready": pipeline.get("required_stages_ready") is True,
    }


@router.get("/repositories/refresh-config")
async def get_repository_refresh_configuration(
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return non-secret monthly refresh readiness for operators/MAST."""
    return refresh_configuration(settings)


@router.post("/repositories/refresh-all", status_code=status.HTTP_202_ACCEPTED)
async def post_repository_refresh_all(
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Start the monthly GitHub snapshot refresh and Intelligence run."""

    async def ingest(content: bytes, filename: str, expected_id: str) -> dict[str, Any]:
        return await _ingest_repository_content(
            content, filename, settings, expected_repository_id=expected_id
        )

    try:
        return start_refresh_job(settings, ingest)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except RuntimeError as error:
        message = str(error)
        code = status.HTTP_409_CONFLICT if "already running" in message.lower() else status.HTTP_503_SERVICE_UNAVAILABLE
        raise HTTPException(status_code=code, detail=message) from error


@router.get("/repositories/refresh-jobs/{job_id}")
async def get_repository_refresh_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    payload = get_refresh_job(settings, job_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown repository refresh job: {job_id}")
    return payload


@router.get("/repositories")
async def get_repositories(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    summaries = list_repositories()
    readiness = _repository_memory_readiness(
        settings,
        [summary.repository_id for summary in summaries],
    )
    return {
        "repositories": [
            {**summary.__dict__, **readiness.get(summary.repository_id, {})}
            for summary in summaries
        ]
    }


@router.get("/repositories/{repository_id}")
async def get_repository_manifest(repository_id: str) -> dict[str, object]:
    record = get_repository(repository_id)
    if record is None:
        raise _not_found(repository_id)
    payload = record.manifest.public_payload()
    payload["rehydrated"] = is_rehydrated(record)
    return payload


@router.get("/repositories/{repository_id}/diff")
async def get_repository_diff(repository_id: str) -> dict[str, list[str]]:
    try:
        diff = repository_diff(repository_id)
    except RepositoryWorkdirUnavailableError as error:
        raise _workdir_unavailable(error) from error
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
    except RepositoryWorkdirUnavailableError as error:
        raise _workdir_unavailable(error) from error
    except RepositoryManagerError as error:
        raise _not_found(repository_id) from error

    payload = manifest.public_payload()
    r2_persisted = _persist_manifest_to_r2(payload, settings)
    payload["r2_persisted"] = r2_persisted
    return payload


@router.delete("/repositories/{repository_id}")
async def delete_repository(
    repository_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    removed = cleanup_repository(repository_id)
    if not removed:
        raise _not_found(repository_id)
    durable = _delete_repository_artifacts(repository_id, settings)
    return {"repository_id": repository_id, "removed": True, **durable}


@router.post("/repositories/cleanup")
async def post_cleanup_expired(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    removed = cleanup_expired_repositories(ttl_seconds=settings.repository_ttl_seconds)
    return {"removed": removed, "removed_count": len(removed)}

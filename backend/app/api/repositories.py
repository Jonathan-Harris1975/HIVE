from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, cast

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import Settings, get_settings
from app.core.governed_repositories import GOVERNED_REPOSITORY_IDS
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
from app.services.repository_memory import ALL_FIELDS, LANE, SCALAR_FIELDS as SCALAR_FIELDS, repository_memory_item_id
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
    """Return snapshot-aware Memory/Intelligence readiness for repository cards."""
    readiness = {
        repository_id: {
            "snapshot_status": "not_loaded",
            "memory_status": "unavailable",
            "memory_ready": False,
            "qa_status": "not_ready",
            "council_status": "not_ready",
            "intelligence_status": "not_ready",
            "intelligence_ready": False,
            "ai_search_status": "not_ready",
            "latest_refresh_time": None,
            "source_fingerprint": None,
            "indexed_version": None,
            "last_pipeline_failure": None,
            "repair_required": True,
            "memory_populated_fields": [],
        }
        for repository_id in repository_ids
    }
    if not repository_ids:
        return readiness

    records = {repository_id: get_repository(repository_id) for repository_id in repository_ids}
    for repository_id, record in records.items():
        if record is not None:
            readiness[repository_id].update(
                snapshot_status="loaded",
                source_fingerprint=record.manifest.fingerprint,
                indexed_version=getattr(record.manifest, "indexed_version", None),
            )

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

    def fingerprint_for(entry: object) -> str:
        if not isinstance(entry, dict):
            return ""
        identity = entry.get("snapshot_identity")
        if isinstance(identity, dict) and identity.get("fingerprint"):
            return str(identity.get("fingerprint"))
        context = entry.get("repository_context")
        if isinstance(context, dict) and context.get("fingerprint"):
            return str(context.get("fingerprint"))
        if entry.get("fingerprint"):
            return str(entry.get("fingerprint"))
        return ""

    def latest_dict(value: object) -> dict[str, Any] | None:
        if not isinstance(value, list):
            return None
        return next((cast(dict[str, Any], item) for item in reversed(value) if isinstance(item, dict)), None)

    for repository_id, values in values_by_repository.items():
        record = records.get(repository_id)
        current_fingerprint = record.manifest.fingerprint if record is not None else ""
        populated = sorted(
            field_name
            for field_name, value in values.items()
            if value is not None and value != "" and value != [] and value != {}
        )
        base_profile_fields = {
            "project_manifest",
            "project_dna",
            "architecture_summary",
            "coding_standards",
            "build_profile",
            "deployment_profile",
            "environment_schema",
        }
        profile_ready = base_profile_fields.issubset(populated)

        project_manifest = values.get("project_manifest")
        manifest_fingerprint = fingerprint_for(project_manifest)
        if not manifest_fingerprint and isinstance(project_manifest, dict):
            manifest_fingerprint = str(project_manifest.get("fingerprint") or "")
        snapshot_current = bool(current_fingerprint and manifest_fingerprint == current_fingerprint)

        latest_qa = latest_dict(values.get("qa_history"))
        latest_council = latest_dict(values.get("repository_council_history"))
        latest_intelligence = latest_dict(values.get("repository_intelligence_history"))
        qa_current = bool(current_fingerprint and fingerprint_for(latest_qa) == current_fingerprint)
        council_current = bool(current_fingerprint and fingerprint_for(latest_council) == current_fingerprint)
        intelligence_current = bool(
            current_fingerprint
            and latest_intelligence is not None
            and latest_intelligence.get("repository_id") == repository_id
            and fingerprint_for(latest_intelligence) == current_fingerprint
        )

        index_state = values.get("repository_index_state")
        index_current = bool(current_fingerprint and fingerprint_for(index_state) == current_fingerprint)
        raw_index_status = str(index_state.get("status") or "not_ready") if isinstance(index_state, dict) else "not_ready"
        if index_current:
            ai_search_status = raw_index_status
        elif isinstance(index_state, dict):
            ai_search_status = "stale"
        else:
            ai_search_status = "not_ready"

        pipeline_state = values.get("repository_pipeline_state")
        pipeline_current = bool(current_fingerprint and fingerprint_for(pipeline_state) == current_fingerprint)
        last_failure = (
            pipeline_state.get("last_pipeline_failure")
            if pipeline_current and isinstance(pipeline_state, dict)
            else None
        )
        latest_refresh_time = None
        if isinstance(project_manifest, dict):
            identity = project_manifest.get("snapshot_identity")
            if isinstance(identity, dict):
                latest_refresh_time = identity.get("refresh_timestamp")

        memory_ready = profile_ready and snapshot_current
        repair_required = not (memory_ready and qa_current and council_current and intelligence_current)
        readiness[repository_id] = {
            "snapshot_status": "current" if snapshot_current else ("stale" if record is not None else "not_loaded"),
            "memory_status": "ready" if memory_ready else ("stale" if profile_ready else ("partial" if populated else "empty")),
            "profile_ready": profile_ready,
            "memory_ready": memory_ready,
            "qa_status": "current" if qa_current else ("stale" if latest_qa else "not_ready"),
            "council_status": "current" if council_current else ("stale" if latest_council else "not_ready"),
            "intelligence_status": "current" if intelligence_current else ("stale" if latest_intelligence else "not_ready"),
            "intelligence_ready": intelligence_current,
            "ai_search_status": ai_search_status,
            "latest_refresh_time": latest_refresh_time,
            "source_fingerprint": current_fingerprint or None,
            "indexed_version": getattr(record.manifest, "indexed_version", None) if record is not None else None,
            "source_commit_sha": getattr(record.manifest, "source_commit_sha", None) if record is not None else None,
            "last_pipeline_failure": last_failure,
            "repair_required": repair_required,
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
    record = get_repository(manifest.repository_id)
    payload["outcome"] = record.ingestion_outcome if record is not None else "success"
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


def _safe_ingestion_error(error: Exception) -> str:
    if isinstance(error, RepositoryManagerError):
        return str(error)
    if isinstance(error, HTTPException):
        return str(error.detail)
    return "Repository ingestion failed; inspect HIVE server logs for the internal error."


@router.post("/repositories/bulk")
async def upload_repositories_bulk(
    uploads: list[UploadFile] = File(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Ingest multiple repository ZIPs independently with bounded concurrency."""
    if not settings.repository_manager_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Repository Manager disabled",
        )
    if not uploads:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one ZIP is required")
    if len(uploads) > settings.repository_bulk_max_count:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Repository batch has {len(uploads)} items; "
                f"limit is {settings.repository_bulk_max_count}"
            ),
        )

    upload_limit = int(settings.max_upload_bytes)
    batch_limit = int(settings.repository_bulk_max_total_bytes)
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for index, upload in enumerate(uploads):
        filename = upload.filename or f"repository-{index + 1}.zip"
        if not filename.lower().endswith(".zip"):
            items.append(
                {
                    "index": index,
                    "filename": filename,
                    "preflight_error": "Repository upload must be a .zip archive.",
                }
            )
            continue
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > batch_limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Repository batch exceeds REPOSITORY_BULK_MAX_TOTAL_BYTES ({batch_limit} bytes)",
            )
        if len(content) > upload_limit:
            items.append(
                {
                    "index": index,
                    "filename": filename,
                    "preflight_error": f"Repository upload exceeds MAX_UPLOAD_BYTES ({upload_limit} bytes)",
                }
            )
            continue
        items.append(
            {
                "index": index,
                "filename": filename,
                "content": content,
                "archive_sha256": hashlib.sha256(content).hexdigest(),
            }
        )

    first_by_digest: dict[str, int] = {}
    for item in items:
        digest = item.get("archive_sha256")
        if not isinstance(digest, str):
            continue
        if digest in first_by_digest:
            item["duplicate_of"] = first_by_digest[digest]
        else:
            first_by_digest[digest] = int(item["index"])

    semaphore = asyncio.Semaphore(settings.repository_bulk_concurrency)
    results_by_index: dict[int, dict[str, Any]] = {}

    async def ingest_one(item: dict[str, Any]) -> None:
        index = int(item["index"])
        filename = str(item["filename"])
        if item.get("preflight_error"):
            results_by_index[index] = {
                "index": index,
                "filename": filename,
                "outcome": "failed",
                "ok": False,
                "error": str(item["preflight_error"]),
                "retryable": True,
            }
            return
        if item.get("duplicate_of") is not None:
            return
        async with semaphore:
            try:
                payload = await _ingest_repository_content(
                    cast(bytes, item["content"]),
                    filename,
                    settings,
                )
                pipeline = payload.get("pipeline")
                pipeline_ready = isinstance(pipeline, dict) and pipeline.get("required_stages_ready") is True
                outcome = str(payload.get("outcome") or "success")
                results_by_index[index] = {
                    "index": index,
                    "filename": filename,
                    "outcome": outcome if pipeline_ready else "failed",
                    "ok": pipeline_ready,
                    "repository_id": payload.get("repository_id"),
                    "fingerprint": payload.get("fingerprint"),
                    "indexed_version": payload.get("indexed_version"),
                    "pipeline_status": pipeline.get("status") if isinstance(pipeline, dict) else None,
                    "retryable": not pipeline_ready,
                    **(
                        {}
                        if pipeline_ready
                        else {"error": "Repository snapshot was accepted but Memory/Intelligence setup is incomplete."}
                    ),
                }
            except Exception as error:  # noqa: BLE001 - per-item isolation is the contract
                results_by_index[index] = {
                    "index": index,
                    "filename": filename,
                    "outcome": "failed",
                    "ok": False,
                    "error": _safe_ingestion_error(error),
                    "retryable": True,
                }

    await asyncio.gather(*(ingest_one(item) for item in items))

    for item in items:
        duplicate_of = item.get("duplicate_of")
        if duplicate_of is None:
            continue
        index = int(item["index"])
        source = results_by_index.get(int(duplicate_of), {})
        if source.get("ok"):
            results_by_index[index] = {
                "index": index,
                "filename": str(item["filename"]),
                "outcome": "duplicate",
                "ok": True,
                "duplicate_of": int(duplicate_of),
                "repository_id": source.get("repository_id"),
                "fingerprint": source.get("fingerprint"),
                "indexed_version": source.get("indexed_version"),
                "retryable": False,
            }
        else:
            results_by_index[index] = {
                "index": index,
                "filename": str(item["filename"]),
                "outcome": "failed",
                "ok": False,
                "duplicate_of": int(duplicate_of),
                "error": "Duplicate archive matches a batch item that failed ingestion.",
                "retryable": True,
            }

    results = [results_by_index[index] for index in range(len(items))]
    failed_count = sum(1 for item in results if not item.get("ok"))
    return {
        "ok": failed_count == 0,
        "repository_count": len(results),
        "completed_count": len(results),
        "failed_count": failed_count,
        "total_bytes": total_bytes,
        "concurrency_limit": settings.repository_bulk_concurrency,
        "results": results,
    }


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


@router.get("/repositories/estate/readiness")
async def get_repository_estate_readiness(
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    repository_ids = list(GOVERNED_REPOSITORY_IDS)
    readiness = _repository_memory_readiness(settings, repository_ids)
    repositories_payload = [
        {"repository_id": repository_id, **readiness[repository_id]}
        for repository_id in repository_ids
    ]
    ready_count = sum(1 for item in repositories_payload if item.get("repair_required") is False)
    return {
        "governed_repository_count": len(repository_ids),
        "ready_count": ready_count,
        "all_current": ready_count == len(repository_ids),
        "repositories": repositories_payload,
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

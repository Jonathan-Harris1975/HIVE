from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.core.config import Settings
from app.services.model_router import ModelRouter, TaskType
from app.services.openrouter import OpenRouterClient
from app.services.repository_learning import record_patch_outcome, update_project_dna
from app.services.repository_manager import (
    RepositoryManagerError,
    RepositoryRecord,
    get_repository,
    is_rehydrated,
)
from app.services.repository_memory import append_history_entry, get_memory_field
from app.services.repository_qa import run_repository_qa_for_workdir
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage

logger = logging.getLogger("uvicorn.error.hive.repository_improvements")

_IMPROVEMENT_LANE = "repository_improvements"
_IMPROVEMENT_SOURCE_TYPE = "repository_improvement_job"
_JOB_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
_MAX_JOBS = 30
_TERMINAL_STATUSES = {"completed", "failed"}

_MAX_CONTEXT_FILES = 18
_MAX_CONTEXT_CHARS = 140_000
_MAX_CHANGE_FILES = 24
_MAX_GENERATED_CHARS = 800_000
_MAX_FILE_CHARS = 240_000

_TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".json",
    ".md", ".txt", ".toml", ".yaml", ".yml", ".css", ".scss", ".html", ".sh",
}
_ROOT_CONTEXT_FILES = (
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
    "vite.config.js",
    "Dockerfile",
    "wrangler.toml",
    "koyeb.yaml",
)
_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}
_PATH_LIKE_RE = re.compile(r"(?:^|[\s'\"`])([A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+\-]+)+\.[A-Za-z0-9]+)")

_MODEL_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(
        r"""(?i)((?:api[_-]?key|secret|token|password)\s*[:=]\s*['"])[^'"\r\n]{8,}(['"])""",
    ),
    re.compile(r"""(?i)(authorization\s*[:=]\s*['"]?bearer\s+)[A-Za-z0-9._~+\-/=]{8,}"""),
)


class RepositoryImprovementError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _jobs_root(settings: Settings) -> Path:
    base = Path(settings.repository_temp_dir or tempfile.gettempdir()) / "hive-repository-improvements"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _normalise_relative_path(raw_path: object) -> str:
    raw = str(raw_path or "").replace("\\", "/").strip()
    if raw.startswith("/"):
        raise RepositoryImprovementError(f"Model returned an unsafe repository path: {raw_path!r}")
    value = raw
    path = Path(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositoryImprovementError(f"Model returned an unsafe repository path: {raw_path!r}")
    if path.name.lower() in _SENSITIVE_NAMES or any(part == ".git" for part in path.parts):
        raise RepositoryImprovementError(f"Model attempted to modify a protected path: {value}")
    return path.as_posix()


def _safe_target(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise RepositoryImprovementError(f"Improvement path escaped repository root: {relative_path}")
    return target


def _redact_model_context(text: str) -> str:
    redacted = text
    for pattern in _MODEL_SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(r"\1[HIVE REDACTED]\2", redacted)
        elif pattern.groups == 1:
            redacted = pattern.sub(r"\1[HIVE REDACTED]", redacted)
        else:
            redacted = pattern.sub("[HIVE REDACTED]", redacted)
    return redacted


def _extract_latest_intelligence(settings: Settings, repository_id: str) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    field = get_memory_field(
        store,
        repository_id=repository_id,
        field_name="repository_intelligence_history",
    )
    entries = field.content if field and isinstance(field.content, list) else []
    for entry in reversed(entries):
        if isinstance(entry, dict):
            return cast(dict[str, Any], entry)
    raise RepositoryImprovementError(
        f"No Repository Intelligence report exists for {repository_id}. Run Repository Intelligence first."
    )


def _current_intelligence(
    settings: Settings,
    record: RepositoryRecord,
) -> dict[str, Any]:
    intelligence = _extract_latest_intelligence(settings, record.repository_id)
    raw_context = intelligence.get("repository_context")
    context = cast(dict[str, Any], raw_context) if isinstance(raw_context, dict) else {}
    report_repository_id = str(intelligence.get("repository_id") or "")
    context_repository_id = str(context.get("repository_id") or "")
    if report_repository_id != record.repository_id or context_repository_id != record.repository_id:
        raise RepositoryImprovementError(
            "Repository Intelligence belongs to a different repository. "
            "Run Repository Intelligence again for the selected repository before applying improvements."
        )
    report_fingerprint = str(context.get("fingerprint") or "")
    if not report_fingerprint or report_fingerprint != record.manifest.fingerprint:
        raise RepositoryImprovementError(
            "Repository Intelligence is stale for the current snapshot. "
            "Run Repository Intelligence again before applying improvements."
        )
    return intelligence


def _require_actionable_findings(intelligence: dict[str, Any]) -> None:
    raw_summary = intelligence.get("summary")
    summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
    try:
        finding_count = int(summary.get("finding_count") or 0)
    except (TypeError, ValueError):
        finding_count = 0
    if finding_count <= 0:
        raise RepositoryImprovementError(
            "Repository Intelligence has no actionable findings for the current snapshot."
        )


def _qa_warning_names(payload: dict[str, Any]) -> set[str]:
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list):
        return set()
    return {
        str(check.get("name"))
        for check in raw_checks
        if isinstance(check, dict)
        and check.get("status") == "warning"
        and isinstance(check.get("name"), str)
    }


def _baseline_qa_warning_names(intelligence: dict[str, Any]) -> set[str]:
    raw_findings = intelligence.get("findings")
    if not isinstance(raw_findings, list):
        return set()
    return {
        str(finding.get("category"))
        for finding in raw_findings
        if isinstance(finding, dict)
        and finding.get("source") == "repository_qa"
        and isinstance(finding.get("category"), str)
    }


def _walk_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _walk_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_strings(nested)


def _candidate_paths(record: RepositoryRecord, intelligence: dict[str, Any]) -> list[str]:
    root = record.workdir
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        clean = raw.replace("\\", "/").strip().lstrip("/")
        if not clean or clean in seen:
            return
        try:
            path = _safe_target(root, _normalise_relative_path(clean))
        except RepositoryImprovementError:
            return
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            return
        if path.stat().st_size > _MAX_FILE_CHARS * 4:
            return
        seen.add(clean)
        candidates.append(clean)

    for text in _walk_strings(intelligence.get("findings", [])):
        if len(text) <= 500:
            try:
                if (root / text).is_file():
                    add(text)
            except OSError:
                pass
        for match in _PATH_LIKE_RE.finditer(text):
            add(match.group(1))

    for name in _ROOT_CONTEXT_FILES:
        add(name)

    # If findings are repository-wide (documentation, architecture, tests) and
    # contain no path evidence, provide representative source files rather than
    # sending the model a generic prompt with no code context.
    if len(candidates) < 8:
        for path in sorted(root.rglob("*")):
            if len(candidates) >= _MAX_CONTEXT_FILES:
                break
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            relative = path.relative_to(root)
            if any(part in {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__"} for part in relative.parts):
                continue
            add(relative.as_posix())

    return candidates[:_MAX_CONTEXT_FILES]


def _read_context_files(record: RepositoryRecord, intelligence: dict[str, Any]) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    used = 0
    for relative in _candidate_paths(record, intelligence):
        path = _safe_target(record.workdir, relative)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        remaining = _MAX_CONTEXT_CHARS - used
        if remaining <= 0:
            break
        if len(text) > min(_MAX_FILE_CHARS, remaining):
            text = text[: min(_MAX_FILE_CHARS, remaining)] + "\n/* HIVE CONTEXT TRUNCATED */\n"
        text = _redact_model_context(text)
        used += len(text)
        context.append({"path": relative, "content": text})
    return context


def _model_request(
    repository_id: str,
    intelligence: dict[str, Any],
    files: list[dict[str, str]],
) -> tuple[str, str]:
    context = intelligence.get("repository_context") if isinstance(intelligence.get("repository_context"), dict) else {}
    summary = intelligence.get("summary") if isinstance(intelligence.get("summary"), dict) else {}
    findings = intelligence.get("findings") if isinstance(intelligence.get("findings"), list) else []

    system = (
        "You are HIVE's repository improvement engine. Produce minimal, production-grade code changes from "
        "the supplied Repository Intelligence evidence. Work only from evidence and file content supplied. "
        "Do not invent unavailable APIs, secrets, services or files. Do not weaken tests, security checks, "
        "lint rules or type gates to make failures disappear. Return one JSON object and no markdown."
    )
    user = json.dumps(
        {
            "task": "Apply the Repository Intelligence findings to an isolated repository copy.",
            "repository_id": repository_id,
            "repository_context": context,
            "summary": summary,
            "findings": findings,
            "repository_improvement_prompt": intelligence.get("improvement_prompt"),
            "files": files,
            "required_output": {
                "summary": "Short repository-specific description of the improvements made.",
                "changes": [
                    {
                        "path": "relative/path.ext",
                        "action": "replace | create | delete",
                        "content": "Complete file content for replace/create; omit or null for delete.",
                        "rationale": "Which Intelligence finding this change addresses.",
                    }
                ],
                "remaining_risks": ["Anything that still requires native CI, deployment or human verification."],
            },
            "rules": [
                "Return complete replacement content, never ellipses or partial snippets.",
                "Only modify files needed to address the supplied findings.",
                "Never write .env, private keys, credentials, tokens or generated dependency/vendor directories.",
                "Preserve public API and deployment contracts unless evidence requires a compatible migration.",
                f"Return no more than {_MAX_CHANGE_FILES} file changes.",
                "If no safe code change is justified, return an empty changes array and explain why in remaining_risks.",
            ],
        },
        ensure_ascii=False,
    )
    return system, user


def _assistant_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "".join(parts).strip()
    return ""


def _parse_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise RepositoryImprovementError("Coding model did not return a JSON improvement payload")
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RepositoryImprovementError("Coding model returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RepositoryImprovementError("Coding model improvement payload must be a JSON object")
    return cast(dict[str, Any], parsed)


def _validated_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list):
        raise RepositoryImprovementError("Coding model response is missing a changes array")
    if len(raw_changes) > _MAX_CHANGE_FILES:
        raise RepositoryImprovementError(
            f"Coding model proposed {len(raw_changes)} files; maximum is {_MAX_CHANGE_FILES}"
        )

    changes: list[dict[str, Any]] = []
    generated_chars = 0
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise RepositoryImprovementError("Every coding-model change must be an object")
        path = _normalise_relative_path(raw.get("path"))
        action = str(raw.get("action") or "replace").strip().lower()
        if action not in {"replace", "create", "delete"}:
            raise RepositoryImprovementError(f"Unsupported improvement action {action!r} for {path}")
        content = raw.get("content")
        if action != "delete":
            if not isinstance(content, str):
                raise RepositoryImprovementError(f"Improvement for {path} is missing complete file content")
            if len(content) > _MAX_FILE_CHARS:
                raise RepositoryImprovementError(f"Improvement for {path} exceeds the per-file output limit")
            generated_chars += len(content)
        if generated_chars > _MAX_GENERATED_CHARS:
            raise RepositoryImprovementError("Coding model output exceeds the total generated-content limit")
        changes.append(
            {
                "path": path,
                "action": action,
                "content": content if action != "delete" else None,
                "rationale": str(raw.get("rationale") or "Repository Intelligence improvement"),
            }
        )
    return changes


def _apply_changes(staging: Path, changes: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    deleted: list[str] = []
    for change in changes:
        relative = str(change["path"])
        target = _safe_target(staging, relative)
        action = str(change["action"])
        if action == "delete":
            if target.exists() and target.is_file():
                target.unlink()
                deleted.append(relative)
            continue
        if action == "create" and target.exists():
            raise RepositoryImprovementError(f"Coding model attempted to create an existing file: {relative}")
        if action == "replace" and not target.is_file():
            raise RepositoryImprovementError(f"Coding model attempted to replace a missing file: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(change["content"]), encoding="utf-8")
        changed.append(relative)
    return changed, deleted


def _zip_tree(root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".venv", "venv"} for part in relative.parts):
                continue
            archive.write(path, arcname=relative.as_posix())


def _zip_changed_files(
    staging: Path,
    destination: Path,
    changed: list[str],
    deleted: list[str],
    report: dict[str, Any],
) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in changed:
            path = _safe_target(staging, relative)
            if path.is_file():
                archive.write(path, arcname=relative)
        archive.writestr("HIVE-IMPROVEMENT-REPORT.json", json.dumps(report, ensure_ascii=False, indent=2))
        if deleted:
            archive.writestr("HIVE-DELETED-FILES.txt", "\n".join(deleted) + "\n")


def _persist_job(settings: Settings, payload: dict[str, Any]) -> None:
    store = D1MetadataStore(settings)
    if not store.enabled:
        return
    persisted = {key: value for key, value in payload.items() if key not in {"local_artifacts"}}
    try:
        store.upsert_metadata(
            item_id=f"repository-improvement:{payload['job_id']}",
            lane=_IMPROVEMENT_LANE,
            source_type=_IMPROVEMENT_SOURCE_TYPE,
            source_id=str(payload["repository_id"]),
            title=f"Repository improvement {payload['repository_id']}",
            url=None,
            metadata=persisted,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not persist repository improvement job %s", payload.get("job_id"))


def _set_job(settings: Settings, job_id: str, **changes: Any) -> dict[str, Any]:
    with _JOB_LOCK:
        current = dict(_JOBS.get(job_id) or {"job_id": job_id})
        current.update(changes)
        current["updated_at"] = _now_iso()
        _JOBS[job_id] = current
        while len(_JOBS) > _MAX_JOBS:
            oldest_id = min(_JOBS, key=lambda key: str(_JOBS[key].get("created_at") or ""))
            if oldest_id == job_id:
                break
            _JOBS.pop(oldest_id, None)
        snapshot = dict(current)
    _persist_job(settings, snapshot)
    return snapshot


def _stored_jobs(settings: Settings) -> list[dict[str, Any]]:
    store = D1MetadataStore(settings)
    if not store.enabled:
        return []
    try:
        result = store.list_metadata(lane=_IMPROVEMENT_LANE, limit=_MAX_JOBS)
    except Exception:  # noqa: BLE001
        return []
    raw_items = result.get("items")
    if not result.get("ok") or not isinstance(raw_items, list):
        return []
    jobs: list[dict[str, Any]] = []
    for row in raw_items:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            jobs.append(cast(dict[str, Any], metadata))
    return jobs


def get_improvement_job(settings: Settings, repository_id: str, job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        local = _JOBS.get(job_id)
        task = _TASKS.get(job_id)
        if local is not None and local.get("repository_id") == repository_id:
            payload = dict(local)
            if payload.get("status") in {"accepted", "running"} and task is not None and task.done():
                payload = _set_job(
                    settings,
                    job_id,
                    status="failed",
                    finished_at=_now_iso(),
                    error="Repository improvement worker stopped before terminal completion.",
                )
            return payload

    for stored in _stored_jobs(settings):
        if stored.get("job_id") != job_id or stored.get("repository_id") != repository_id:
            continue
        if stored.get("status") not in _TERMINAL_STATUSES:
            changes = dict(stored)
            changes.pop("job_id", None)
            changes.update(
                status="failed",
                finished_at=_now_iso(),
                error="HIVE restarted while this improvement job was active. Start a new improvement run.",
            )
            return _set_job(settings, job_id, **changes)
        return stored
    return None


def latest_improvement_job(settings: Settings, repository_id: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    with _JOB_LOCK:
        candidates.extend(
            dict(job) for job in _JOBS.values() if job.get("repository_id") == repository_id
        )
    candidates.extend(job for job in _stored_jobs(settings) if job.get("repository_id") == repository_id)
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))


def active_improvement_job(repository_id: str) -> str | None:
    with _JOB_LOCK:
        for job_id, payload in _JOBS.items():
            if payload.get("repository_id") != repository_id:
                continue
            if payload.get("status") in {"accepted", "running"}:
                task = _TASKS.get(job_id)
                if task is None or not task.done():
                    return job_id
    return None


async def _run_model(
    settings: Settings,
    repository_id: str,
    intelligence: dict[str, Any],
    files: list[dict[str, str]],
) -> tuple[dict[str, Any], str | None]:
    router = ModelRouter(settings)
    model = router.select_model(TaskType.CODE)
    fallbacks = router.fallback_models_for_task(TaskType.CODE, model)
    system, user = _model_request(repository_id, intelligence, files)
    response = await OpenRouterClient(settings).chat_completion(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 16_000,
        },
        fallback_models=fallbacks,
    )
    if response.get("_all_attempts_failed"):
        raise RepositoryImprovementError(_assistant_text(response) or "Coding model failed")
    parsed = _parse_json_object(_assistant_text(response))
    return parsed, str(response.get("model") or model)


def _store_artifact(
    settings: Settings,
    path: Path,
    *,
    repository_id: str,
    job_id: str,
    name: str,
) -> tuple[str | None, bool]:
    storage = R2Storage(settings)
    if not settings.r2_bucket_repositories or not storage.write_enabled:
        if settings.production_require_r2:
            raise RepositoryImprovementError("R2 repository storage is required for durable improvement downloads")
        return None, False
    key = f"improvements/{repository_id}/{job_id}/{name}"
    storage.put_file(path, key, content_type="application/zip", bucket=settings.r2_bucket_repositories)
    return key, True


async def _run_job(settings: Settings, job_id: str, repository_id: str) -> None:
    job_root = _jobs_root(settings) / job_id
    staging = job_root / "workspace"
    job_root.mkdir(parents=True, exist_ok=True)
    _set_job(settings, job_id, status="running", started_at=_now_iso(), stage="loading_intelligence")
    try:
        record = get_repository(repository_id)
        if record is None:
            raise RepositoryManagerError(f"Unknown repository_id: {repository_id}")
        if is_rehydrated(record):
            raise RepositoryImprovementError(
                f"Repository {repository_id} has no usable source snapshot. Upload or restore it before improvements."
            )

        intelligence = _current_intelligence(settings, record)
        _require_actionable_findings(intelligence)

        _set_job(settings, job_id, stage="preparing_workspace", source_fingerprint=record.manifest.fingerprint)
        shutil.copytree(record.workdir, staging, dirs_exist_ok=False)
        context_files = _read_context_files(record, intelligence)
        if not context_files:
            raise RepositoryImprovementError("No suitable repository text files were available for the coding model")

        _set_job(settings, job_id, stage="coding_model", context_file_count=len(context_files))
        model_payload, model_used = await _run_model(settings, repository_id, intelligence, context_files)
        changes = _validated_changes(model_payload)
        if not changes:
            raise RepositoryImprovementError(
                "Coding model did not identify a safe file change. Review the remaining risks in Repository Intelligence."
            )

        _set_job(settings, job_id, stage="applying_changes", model_used=model_used, proposed_change_count=len(changes))
        changed, deleted = _apply_changes(staging, changes)
        if not changed and not deleted:
            raise RepositoryImprovementError("Coding model changes produced no repository modifications")

        _set_job(settings, job_id, stage="static_validation")
        qa_after = run_repository_qa_for_workdir(
            repository_id,
            staging,
            manifest_dependencies=[dep.__dict__ for dep in record.manifest.dependencies],
        ).public_payload()
        baseline_warnings = _baseline_qa_warning_names(intelligence)
        after_warnings = _qa_warning_names(qa_after)
        new_warning_checks = sorted(after_warnings - baseline_warnings)
        if new_warning_checks:
            raise RepositoryImprovementError(
                "Generated improvement introduced new Repository QA warning check(s): "
                + ", ".join(new_warning_checks)
                + "; no downloadable artifact was published."
            )
        raw_summary = intelligence.get("summary")
        baseline_summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
        try:
            baseline_qa_score = float(baseline_summary.get("qa_score") or 0.0)
            after_qa_score = float(qa_after.get("score") or 0.0)
        except (TypeError, ValueError):
            baseline_qa_score = 0.0
            after_qa_score = 0.0
        if after_qa_score + 0.001 < baseline_qa_score:
            raise RepositoryImprovementError(
                f"Generated improvement reduced static Repository QA score from {baseline_qa_score:.3f} "
                f"to {after_qa_score:.3f}; no downloadable artifact was published."
            )
        build_check = next(
            (item for item in qa_after.get("checks", []) if isinstance(item, dict) and item.get("name") == "build_verification"),
            None,
        )
        security_check = next(
            (item for item in qa_after.get("checks", []) if isinstance(item, dict) and item.get("name") == "security_scanning"),
            None,
        )
        if isinstance(build_check, dict) and build_check.get("status") == "warning":
            raise RepositoryImprovementError(
                "Generated improvement failed HIVE static build verification; no downloadable artifact was published."
            )

        # Repository QA secret scanning is intentionally heuristic. A warning that
        # already existed on the source snapshot must not make the improvement
        # service unusable, and recognised fixtures/examples are filtered by QA.
        # A *new* security_scanning warning is still blocked above via
        # new_warning_checks, so generated code cannot introduce a new candidate.
        security_validation = {
            "status": str(security_check.get("status") or "unknown") if isinstance(security_check, dict) else "unknown",
            "details": security_check.get("details", {}) if isinstance(security_check, dict) else {},
            "blocking_policy": "new_warning_only",
        }

        summary = str(model_payload.get("summary") or f"Automated improvements for {repository_id}")
        raw_risks = model_payload.get("remaining_risks")
        remaining_risks = [str(item) for item in raw_risks] if isinstance(raw_risks, list) else []
        native_ci_risk = (
            "HIVE performs static repository validation on the generated copy but does not install dependencies "
            "or execute the repository's native CI/build/test suite. Run the repository's normal CI before deployment."
        )
        if native_ci_risk not in remaining_risks:
            remaining_risks.append(native_ci_risk)
        report = {
            "job_id": job_id,
            "repository_id": repository_id,
            "source_fingerprint": record.manifest.fingerprint,
            "model_used": model_used,
            "summary": summary,
            "changes": [
                {key: value for key, value in change.items() if key != "content"}
                for change in changes
            ],
            "changed_files": changed,
            "deleted_files": deleted,
            "remaining_risks": remaining_risks,
            "static_validation": qa_after,
            "security_validation": security_validation,
            "generated_at": _now_iso(),
        }

        changed_zip = job_root / f"{repository_id}-improved-files.zip"
        full_zip = job_root / f"{repository_id}-improved-repository.zip"
        _zip_changed_files(staging, changed_zip, changed, deleted, report)
        _zip_tree(staging, full_zip)

        _set_job(settings, job_id, stage="persisting_artifacts")
        changed_key, changed_durable = _store_artifact(
            settings,
            changed_zip,
            repository_id=repository_id,
            job_id=job_id,
            name="changed-files.zip",
        )
        full_key, full_durable = _store_artifact(
            settings,
            full_zip,
            repository_id=repository_id,
            job_id=job_id,
            name="updated-repository.zip",
        )

        store = D1MetadataStore(settings)
        if store.enabled:
            append_history_entry(
                store,
                repository_id=repository_id,
                field_name="optimisation_history",
                entry={
                    "occurred_at": _now_iso(),
                    "job_id": job_id,
                    "summary": summary,
                    "model_used": model_used,
                    "changed_files": changed,
                    "deleted_files": deleted,
                    "remaining_risks": remaining_risks,
                    "qa_score_after": qa_after.get("score"),
                },
            )
            record_patch_outcome(
                settings,
                repository_id=repository_id,
                summary=summary,
                success=True,
                files_changed=[*changed, *deleted],
            )
            update_project_dna(settings, repository_id=repository_id)

        _set_job(
            settings,
            job_id,
            status="completed",
            stage="completed",
            finished_at=_now_iso(),
            ok=True,
            model_used=model_used,
            summary=summary,
            changed_files=changed,
            deleted_files=deleted,
            change_count=len(changed) + len(deleted),
            remaining_risks=remaining_risks,
            qa_score_after=qa_after.get("score"),
            artifacts={
                "changed_files": {
                    "filename": changed_zip.name,
                    "r2_key": changed_key,
                    "durable": changed_durable,
                },
                "updated_repository": {
                    "filename": full_zip.name,
                    "r2_key": full_key,
                    "durable": full_durable,
                },
            },
            local_artifacts={
                "changed_files": str(changed_zip),
                "updated_repository": str(full_zip),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Repository improvement failed repository_id=%s job_id=%s", repository_id, job_id)
        _set_job(
            settings,
            job_id,
            status="failed",
            stage="failed",
            finished_at=_now_iso(),
            ok=False,
            error=str(exc),
        )
    finally:
        # Keep local packages for development/test downloads. Production requires
        # durable R2 storage, so discard the complete local job directory once the
        # worker has finished to prevent long-running Koyeb instances accumulating
        # repository copies on ephemeral disk.
        shutil.rmtree(staging, ignore_errors=True)
        if settings.production_require_r2:
            shutil.rmtree(job_root, ignore_errors=True)


def start_improvement_job(settings: Settings, repository_id: str) -> dict[str, Any]:
    if not settings.repository_manager_enabled:
        raise RepositoryImprovementError("Repository Manager is disabled")
    if not settings.openrouter_api_key.strip():
        raise RepositoryImprovementError("OPENROUTER_API_KEY is required for automatic repository improvements")

    record = get_repository(repository_id)
    if record is None:
        raise RepositoryManagerError(f"Unknown repository_id: {repository_id}")
    if is_rehydrated(record):
        raise RepositoryImprovementError(
            f"Repository {repository_id} has no usable source snapshot. Upload or restore it first."
        )
    intelligence = _current_intelligence(settings, record)
    _require_actionable_findings(intelligence)

    active = active_improvement_job(repository_id)
    if active:
        raise RepositoryImprovementError(
            f"Repository improvement job {active} is already running for {repository_id}"
        )

    job_id = uuid.uuid4().hex
    payload = _set_job(
        settings,
        job_id,
        repository_id=repository_id,
        status="accepted",
        stage="queued",
        created_at=_now_iso(),
        source_fingerprint=record.manifest.fingerprint,
        ok=None,
    )
    task = asyncio.create_task(_run_job(settings, job_id, repository_id), name=f"repository-improvement-{job_id}")
    with _JOB_LOCK:
        _TASKS[job_id] = task

    def forget_task(_task: asyncio.Task[None]) -> None:
        with _JOB_LOCK:
            _TASKS.pop(job_id, None)

    task.add_done_callback(forget_task)
    return payload


def improvement_artifact(
    settings: Settings,
    repository_id: str,
    job_id: str,
    kind: str,
) -> tuple[str, bytes, str]:
    if kind not in {"changed_files", "updated_repository"}:
        raise RepositoryImprovementError("Unknown improvement artifact kind")
    job = get_improvement_job(settings, repository_id, job_id)
    if job is None:
        raise RepositoryImprovementError("Unknown repository improvement job")
    if job.get("status") != "completed":
        raise RepositoryImprovementError("Repository improvement artifact is not ready")

    artifacts = job.get("artifacts") if isinstance(job.get("artifacts"), dict) else {}
    artifact = artifacts.get(kind) if isinstance(artifacts, dict) else None
    artifact = cast(dict[str, Any], artifact) if isinstance(artifact, dict) else {}
    filename = str(artifact.get("filename") or f"{repository_id}-{kind}.zip")

    local_artifacts = job.get("local_artifacts") if isinstance(job.get("local_artifacts"), dict) else {}
    local_path = local_artifacts.get(kind) if isinstance(local_artifacts, dict) else None
    if isinstance(local_path, str) and Path(local_path).is_file():
        return filename, Path(local_path).read_bytes(), "application/zip"

    r2_key = artifact.get("r2_key")
    if not isinstance(r2_key, str) or not r2_key:
        raise RepositoryImprovementError("Improvement artifact is not available in durable storage")
    storage = R2Storage(settings)
    errors: list[str] = []
    for read_only in (True, False):
        if read_only and not storage.read_enabled:
            continue
        if not read_only and not storage.write_enabled:
            continue
        try:
            obj = storage.read_object(
                r2_key,
                max_bytes=max(int(settings.repository_max_uncompressed_bytes), int(settings.max_upload_bytes)),
                bucket=settings.r2_bucket_repositories,
                read_only=read_only,
            )
            return filename, obj.content, "application/zip"
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RepositoryImprovementError("Improvement artifact download failed: " + "; ".join(errors))

from __future__ import annotations

import asyncio
import json
import logging
import math
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
from app.storage.sql_store import SqlStore

logger = logging.getLogger("uvicorn.error.hive.repository_improvements")

_IMPROVEMENT_LANE = "repository_improvements"
_IMPROVEMENT_SOURCE_TYPE = "repository_improvement_job"
_JOB_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
_MAX_JOBS = 30
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

_MAX_CONTEXT_FILES = 18
_MAX_CONTEXT_CHARS = 140_000
_MAX_GENERATED_CHARS = 800_000
_MAX_FILE_CHARS = 240_000

_TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".css",
    ".scss",
    ".html",
    ".sh",
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
    "credentials.json",
    "secrets.json",
}
_BINARY_ASSET_SUFFIXES = {
    ".7z", ".avi", ".bin", ".bmp", ".class", ".dll", ".dmg", ".doc", ".docx",
    ".exe", ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg", ".mov", ".mp3",
    ".mp4", ".o", ".obj", ".pdf", ".png", ".so", ".tar", ".webp", ".woff",
    ".woff2", ".xls", ".xlsx", ".zip",
}
_PATH_LIKE_RE = re.compile(
    r"(?:^|[\s'\"`])([A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+\-]+)+\.[A-Za-z0-9]+)"
)

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
    base = (
        Path(settings.repository_temp_dir or tempfile.gettempdir()) / "hive-repository-improvements"
    )
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
    lowered_name = path.name.lower()
    if (
        lowered_name in _SENSITIVE_NAMES
        or lowered_name.startswith(".env.")
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
        or any(part == ".git" for part in path.parts)
    ):
        raise RepositoryImprovementError(f"Model attempted to modify a protected path: {value}")
    return path.as_posix()


def _safe_target(root: Path, relative_path: str) -> Path:
    target = (root / relative_path).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise RepositoryImprovementError(
            f"Improvement path escaped repository root: {relative_path}"
        )
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
    if (
        report_repository_id != record.repository_id
        or context_repository_id != record.repository_id
    ):
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


def _candidate_paths(
    record: RepositoryRecord,
    intelligence: dict[str, Any],
    *,
    root: Path | None = None,
) -> list[str]:
    root = root or record.workdir
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
            if any(
                part in {"node_modules", ".git", "dist", "build", ".venv", "venv", "__pycache__"}
                for part in relative.parts
            ):
                continue
            add(relative.as_posix())

    return candidates[:_MAX_CONTEXT_FILES]


def _read_context_files(
    record: RepositoryRecord,
    intelligence: dict[str, Any],
    *,
    root: Path | None = None,
) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    used = 0
    context_root = root or record.workdir
    for relative in _candidate_paths(record, intelligence, root=context_root):
        path = _safe_target(context_root, relative)
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
    *,
    orchestration: dict[str, Any] | None = None,
) -> tuple[str, str]:
    context = (
        intelligence.get("repository_context")
        if isinstance(intelligence.get("repository_context"), dict)
        else {}
    )
    summary = intelligence.get("summary") if isinstance(intelligence.get("summary"), dict) else {}
    findings = (
        intelligence.get("findings") if isinstance(intelligence.get("findings"), list) else []
    )

    stage = str((orchestration or {}).get("stage") or "self_improvement")
    work_scope = (orchestration or {}).get("work_scope")
    raw_effective_file_limit = (
        work_scope.get("effective_file_limit") if isinstance(work_scope, dict) else None
    )
    effective_file_limit = (
        int(raw_effective_file_limit) if raw_effective_file_limit is not None else 1
    )
    if stage == "council":
        system = (
            "You are HIVE's expert repository review council. Review the best candidate produced by the "
            "self-improvement loops and make only the minimum production-grade changes needed to clear the "
            "remaining validation gap. Preserve validated fixes. Work only from supplied evidence and files. "
            "Do not weaken tests, security checks, lint rules or type gates. Return one JSON object and no markdown."
        )
    else:
        system = (
            "You are HIVE's repository improvement engine. Produce minimal, production-grade code changes from "
            "the supplied Repository Intelligence evidence. Improve on the best prior iteration when validation "
            "feedback is supplied. Work only from evidence and file content supplied. Do not invent unavailable "
            "APIs, secrets, services or files. Do not weaken tests, security checks, lint rules or type gates to "
            "make failures disappear. Return one JSON object and no markdown."
        )
    user = json.dumps(
        {
            "task": "Apply the Repository Intelligence findings to an isolated repository copy.",
            "repository_id": repository_id,
            "repository_context": context,
            "summary": summary,
            "findings": findings,
            "repository_improvement_prompt": intelligence.get("improvement_prompt"),
            "orchestration": orchestration or {},
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
                "remaining_findings": [
                    "Any Repository Intelligence finding not addressed by this pass."
                ],
                "remaining_risks": [
                    "Anything that still requires native CI, deployment or human verification."
                ],
            },
            "rules": [
                "Return complete replacement content, never ellipses or partial snippets.",
                "Only modify files needed to address the supplied findings.",
                "Never write .env, private keys, credentials, tokens or generated dependency/vendor directories.",
                "Preserve public API and deployment contracts unless evidence requires a compatible migration.",
                f"Return no more than {effective_file_limit} file changes in this work pass.",
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
            raise RepositoryImprovementError(
                "Coding model did not return a JSON improvement payload"
            )
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RepositoryImprovementError("Coding model returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RepositoryImprovementError("Coding model improvement payload must be a JSON object")
    return cast(dict[str, Any], parsed)


def _validated_changes(payload: dict[str, Any], *, max_change_files: int) -> list[dict[str, Any]]:
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list):
        raise RepositoryImprovementError("Coding model response is missing a changes array")
    if len(raw_changes) > max_change_files:
        raise RepositoryImprovementError(
            f"Coding model proposed {len(raw_changes)} files; work-pass maximum is {max_change_files}"
        )

    changes: list[dict[str, Any]] = []
    generated_chars = 0
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise RepositoryImprovementError("Every coding-model change must be an object")
        path = _normalise_relative_path(raw.get("path"))
        action = str(raw.get("action") or "replace").strip().lower()
        if action not in {"replace", "create", "delete"}:
            raise RepositoryImprovementError(
                f"Unsupported improvement action {action!r} for {path}"
            )
        content = raw.get("content")
        if action != "delete":
            if not isinstance(content, str):
                raise RepositoryImprovementError(
                    f"Improvement for {path} is missing complete file content"
                )
            if len(content) > _MAX_FILE_CHARS:
                raise RepositoryImprovementError(
                    f"Improvement for {path} exceeds the per-file output limit"
                )
            generated_chars += len(content)
        if generated_chars > _MAX_GENERATED_CHARS:
            raise RepositoryImprovementError(
                "Coding model output exceeds the total generated-content limit"
            )
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
            raise RepositoryImprovementError(
                f"Coding model attempted to create an existing file: {relative}"
            )
        if action == "replace" and not target.is_file():
            raise RepositoryImprovementError(
                f"Coding model attempted to replace a missing file: {relative}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(change["content"]), encoding="utf-8")
        changed.append(relative)
    return changed, deleted


def _zip_tree(root: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(
                part
                in {
                    ".git",
                    "node_modules",
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                    "dist",
                    "build",
                    ".venv",
                    "venv",
                }
                for part in relative.parts
            ):
                continue
            archive.write(path, arcname=relative.as_posix())


def _zip_changed_files(
    staging: Path,
    destination: Path,
    changed: list[str],
    deleted: list[str],
    report: dict[str, Any],
) -> None:
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative in changed:
            path = _safe_target(staging, relative)
            if path.is_file():
                archive.write(path, arcname=relative)
        archive.writestr(
            "HIVE-IMPROVEMENT-REPORT.json", json.dumps(report, ensure_ascii=False, indent=2)
        )
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


def get_improvement_job(
    settings: Settings, repository_id: str, job_id: str
) -> dict[str, Any] | None:
    with _JOB_LOCK:
        local = _JOBS.get(job_id)
        task = _TASKS.get(job_id)
        if local is not None and local.get("repository_id") == repository_id:
            payload = dict(local)
            if (
                payload.get("status") in {"accepted", "running"}
                and task is not None
                and task.done()
            ):
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
    candidates.extend(
        job for job in _stored_jobs(settings) if job.get("repository_id") == repository_id
    )
    if not candidates:
        return None
    return max(
        candidates, key=lambda item: str(item.get("created_at") or item.get("updated_at") or "")
    )


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
    *,
    model: str | None = None,
    orchestration: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    router = ModelRouter(settings)
    selected_model = model or router.select_model(TaskType.CODE)
    # Iterative orchestration owns escalation explicitly. When a specific model
    # is supplied, do not allow OpenRouter fallbacks to jump tiers behind the
    # orchestrator's back; the next loop/council run performs that escalation.
    fallbacks = (
        []
        if model is not None
        else router.fallback_models_for_task(TaskType.CODE, selected_model, allow_free=False)
    )
    system, user = _model_request(
        repository_id,
        intelligence,
        files,
        orchestration=orchestration,
    )
    response = await OpenRouterClient(settings).chat_completion(
        {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 16_000,
            "usage": {"include": True},
            "_hive_context_profile": "coding",
        },
        fallback_models=fallbacks,
        allow_implicit_free_fallback=False,
    )
    if response.get("_all_attempts_failed"):
        raise RepositoryImprovementError(_assistant_text(response) or "Coding model failed")
    usage = response.get("usage")
    if isinstance(usage, dict):
        stage = str((orchestration or {}).get("stage") or "self_improvement")
        iteration = (orchestration or {}).get("iteration")
        await asyncio.to_thread(
            SqlStore(settings).record_usage_event,
            conversation_id=f"repository-improvement:{repository_id}",
            model_used=str(response.get("model") or selected_model),
            provider=(str(response.get("provider")) if response.get("provider") else None),
            usage=usage,
            metadata={
                "operation": "repository_improvement",
                "repository_id": repository_id,
                "stage": stage,
                "iteration": iteration,
            },
        )
    parsed = _parse_json_object(_assistant_text(response))
    return parsed, str(response.get("model") or selected_model)


def _qa_check(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return None
    return next(
        (
            item
            for item in checks
            if isinstance(item, dict) and item.get("name") == name
        ),
        None,
    )


def _validate_candidate(
    *,
    repository_id: str,
    root: Path,
    record: RepositoryRecord,
    intelligence: dict[str, Any],
    target_score: float,
    tolerance: float,
) -> dict[str, Any]:
    qa_after = run_repository_qa_for_workdir(
        repository_id,
        root,
        manifest_dependencies=[dep.__dict__ for dep in record.manifest.dependencies],
    ).public_payload()
    baseline_warnings = _baseline_qa_warning_names(intelligence)
    after_warnings = _qa_warning_names(qa_after)
    new_warning_checks = sorted(after_warnings - baseline_warnings)

    raw_summary = intelligence.get("summary")
    baseline_summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
    try:
        baseline_qa_score = float(baseline_summary.get("qa_score") or 0.0)
        after_qa_score = float(qa_after.get("score") or 0.0)
    except (TypeError, ValueError):
        baseline_qa_score = 0.0
        after_qa_score = 0.0

    hard_blockers: list[str] = []
    if new_warning_checks:
        hard_blockers.append("new_qa_warnings:" + ",".join(new_warning_checks))
    build_check = _qa_check(qa_after, "build_verification")
    if isinstance(build_check, dict) and build_check.get("status") == "warning":
        hard_blockers.append("build_verification_warning")
    if after_qa_score + 0.001 < baseline_qa_score:
        hard_blockers.append(
            f"qa_score_regression:{baseline_qa_score:.3f}->{after_qa_score:.3f}"
        )

    eligible = not hard_blockers
    meets_target = eligible and after_qa_score + 0.001 >= target_score
    within_tolerance = (
        eligible
        and not meets_target
        and after_qa_score + tolerance + 0.001 >= target_score
    )
    security_check = _qa_check(qa_after, "security_scanning")
    return {
        "qa": qa_after,
        "score": round(after_qa_score, 3),
        "baseline_score": round(baseline_qa_score, 3),
        "target_score": round(target_score, 3),
        "tolerance": round(tolerance, 3),
        "warning_checks": sorted(after_warnings),
        "new_warning_checks": new_warning_checks,
        "hard_blockers": hard_blockers,
        "eligible": eligible,
        "meets_target": meets_target,
        "within_tolerance": within_tolerance,
        "security_validation": {
            "status": str(security_check.get("status") or "unknown")
            if isinstance(security_check, dict)
            else "unknown",
            "details": security_check.get("details", {})
            if isinstance(security_check, dict)
            else {},
            "blocking_policy": "new_warning_only",
        },
    }


def _ignored_workspace_path(relative: Path) -> bool:
    return any(
        part
        in {
            ".git",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "dist",
            "build",
            ".venv",
            "venv",
        }
        for part in relative.parts
    )


def _explicit_binary_finding_paths(intelligence: dict[str, Any]) -> set[str]:
    context = intelligence.get("repository_context")
    if not isinstance(context, dict):
        return set()
    raw = context.get("implicated_files")
    if not isinstance(raw, list):
        return set()
    return {str(item).replace("\\", "/").lstrip("/") for item in raw if isinstance(item, str)}


def _eligible_modifiable_files(root: Path, intelligence: dict[str, Any]) -> dict[str, Path]:
    explicitly_implicated = _explicit_binary_finding_paths(intelligence)
    eligible: dict[str, Path] = {}
    for relative, path in _workspace_files(root).items():
        rel = Path(relative)
        lowered_name = rel.name.lower()
        if (
            lowered_name in _SENSITIVE_NAMES
            or lowered_name.startswith(".env.")
            or rel.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
        ):
            continue
        if rel.suffix.lower() in _BINARY_ASSET_SUFFIXES and relative not in explicitly_implicated:
            continue
        eligible[relative] = path
    return eligible


def _work_scope_budget(
    settings: Settings,
    root: Path,
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    eligible_count = len(_eligible_modifiable_files(root, intelligence))
    ratio = float(settings.repository_improvement_max_change_ratio)
    ratio_limit = max(1, math.floor(eligible_count * ratio)) if eligible_count else 1
    absolute_limit = max(1, int(settings.repository_improvement_max_change_files))
    effective_limit = min(ratio_limit, absolute_limit)
    return {
        "eligible_file_count": eligible_count,
        "configured_ratio": ratio,
        "percentage_limit": ratio_limit,
        "absolute_file_ceiling": absolute_limit,
        "effective_file_limit": effective_limit,
    }


def _work_scope_usage(
    scope: dict[str, Any],
    changed: list[str],
    deleted: list[str],
) -> dict[str, Any]:
    modified_count = len(set(changed) | set(deleted))
    eligible_count = int(scope.get("eligible_file_count") or 0)
    actual_ratio = (modified_count / eligible_count) if eligible_count else (1.0 if modified_count else 0.0)
    return {
        **scope,
        "files_modified": modified_count,
        "percentage_actually_modified": round(actual_ratio, 6),
    }


def _findings_for_work_pass(
    intelligence: dict[str, Any],
    work_pass: int,
    total_passes: int,
) -> dict[str, Any]:
    """Return a deterministic coherent slice of findings for one work pass."""
    findings = intelligence.get("findings")
    if not isinstance(findings, list) or total_passes <= 1 or len(findings) <= 1:
        return intelligence
    chunk = max(1, math.ceil(len(findings) / total_passes))
    start = min(len(findings), (work_pass - 1) * chunk)
    selected = findings[start : start + chunk]
    if not selected:
        selected = findings[-chunk:]
    return {**intelligence, "findings": selected}


def _workspace_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _ignored_workspace_path(relative):
            continue
        files[relative.as_posix()] = path
    return files


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(1024 * 1024)
                right_chunk = right_handle.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _workspace_diff(source: Path, candidate: Path) -> tuple[list[str], list[str]]:
    source_files = _workspace_files(source)
    candidate_files = _workspace_files(candidate)
    changed = sorted(
        relative
        for relative, candidate_path in candidate_files.items()
        if relative not in source_files or not _same_file(source_files[relative], candidate_path)
    )
    deleted = sorted(relative for relative in source_files if relative not in candidate_files)
    return changed, deleted


def _bounded_history(history: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "work_pass": item.get("work_pass"),
            "stage": item.get("stage"),
            "iteration": item.get("iteration"),
            "model": item.get("model"),
            "status": item.get("status"),
            "qa_score": item.get("qa_score"),
            "hard_blockers": item.get("hard_blockers", []),
            "accepted": item.get("accepted", False),
        }
        for item in history[-limit:]
    ]

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
            raise RepositoryImprovementError(
                "R2 repository storage is required for durable improvement downloads"
            )
        return None, False
    key = f"improvements/{repository_id}/{job_id}/{name}"
    storage.put_file(
        path, key, content_type="application/zip", bucket=settings.r2_bucket_repositories
    )
    return key, True


async def _run_job(
    settings: Settings,
    job_id: str,
    repository_id: str,
    execution_mode: str = "single_pass",
    max_work_passes: int = 1,
) -> None:
    job_root = _jobs_root(settings) / job_id
    staging = job_root / "workspace"
    job_root.mkdir(parents=True, exist_ok=True)
    _set_job(
        settings, job_id, status="running", started_at=_now_iso(), stage="loading_intelligence"
    )
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
        raw_summary = intelligence.get("summary")
        baseline_summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
        try:
            baseline_qa_score = float(baseline_summary.get("qa_score") or 0.0)
        except (TypeError, ValueError):
            baseline_qa_score = 0.0
        target_score = max(
            baseline_qa_score,
            float(settings.repository_improvement_success_threshold),
        )
        target_score = min(1.0, target_score)
        tolerance = float(settings.repository_improvement_near_threshold_tolerance)

        _set_job(
            settings,
            job_id,
            stage="preparing_workspace",
            source_fingerprint=record.manifest.fingerprint,
            target_qa_score=round(target_score, 3),
            near_threshold_tolerance=round(tolerance, 3),
        )
        shutil.copytree(record.workdir, staging, dirs_exist_ok=False)
        initial_context = _read_context_files(record, intelligence, root=staging)
        if not initial_context:
            raise RepositoryImprovementError(
                "No suitable repository text files were available for the coding model"
            )

        router = ModelRouter(settings)
        loop_models = router.progressive_models_for_task(
            TaskType.CODE,
            max_models=settings.repository_improvement_max_loops,
            allow_free=False,
            allow_premium=False,
        )
        if not loop_models:
            raise RepositoryImprovementError(
                "No governed paid coding model is available for repository self-improvement loops"
            )
        council_models = router.council_models_for_task(
            TaskType.CODE,
            max_models=settings.repository_improvement_max_council_runs,
        )

        outcomes: list[dict[str, Any]] = []
        work_pass_ledger: list[dict[str, Any]] = []
        best_score = baseline_qa_score
        selected_payload: dict[str, Any] | None = None
        selected_model: str | None = None
        selected_validation: dict[str, Any] | None = None
        accepted_stage: str | None = None
        accepted_iteration: int | None = None
        accepted_work_pass: int | None = None
        accepted_under_tolerance = False

        normalised_mode = execution_mode.strip().lower()
        if normalised_mode not in {"single_pass", "multi_pass"}:
            raise RepositoryImprovementError("execution_mode must be single_pass or multi_pass")
        configured_pass_cap = max(1, int(settings.repository_improvement_max_work_passes))
        requested_passes = max(1, int(max_work_passes))
        total_work_passes = 1 if normalised_mode == "single_pass" else min(requested_passes, configured_pass_cap)

        for work_pass in range(1, total_work_passes + 1):
            pass_baseline = job_root / f"work-pass-{work_pass}-baseline-workspace"
            shutil.rmtree(pass_baseline, ignore_errors=True)
            shutil.copytree(staging, pass_baseline, dirs_exist_ok=False)
            work_scope = _work_scope_budget(settings, pass_baseline, intelligence)
            pass_start_score = best_score
            pass_outcomes_start = len(outcomes)
            pass_selected_validation: dict[str, Any] | None = None
            pass_intelligence = _findings_for_work_pass(intelligence, work_pass, total_work_passes)

            async def run_attempt(
                *,
                stage: str,
                iteration: int,
                model: str,
                allow_near_threshold: bool,
            ) -> bool:
                nonlocal best_score
                nonlocal selected_payload
                nonlocal selected_model
                nonlocal selected_validation
                nonlocal pass_selected_validation
                nonlocal accepted_stage
                nonlocal accepted_iteration
                nonlocal accepted_work_pass
                nonlocal accepted_under_tolerance

                attempt_root = job_root / f"work-pass-{work_pass}-{stage}-{iteration}-workspace"
                shutil.rmtree(attempt_root, ignore_errors=True)
                shutil.copytree(staging, attempt_root, dirs_exist_ok=False)
                _set_job(
                    settings,
                    job_id,
                    stage=f"work_pass_{work_pass}_{stage}_{iteration}",
                    progress={
                        "execution_mode": normalised_mode,
                        "work_pass": work_pass,
                        "max_work_passes": total_work_passes,
                        "attempt_stage": stage,
                        "attempt_iteration": iteration,
                    },
                    orchestration={
                        "target_qa_score": round(target_score, 3),
                        "near_threshold_tolerance": round(tolerance, 3),
                        "work_pass": work_pass,
                        "max_work_passes": total_work_passes,
                        "work_scope": work_scope,
                        "self_improvement_max_loops": settings.repository_improvement_max_loops,
                        "council_max_runs": settings.repository_improvement_max_council_runs,
                        "outcomes": outcomes,
                    },
                )

                context_files = _read_context_files(record, pass_intelligence, root=staging)
                orchestration_context = {
                    "stage": stage,
                    "iteration": iteration,
                    "work_pass": work_pass,
                    "max_work_passes": total_work_passes,
                    "execution_mode": normalised_mode,
                    "work_scope": work_scope,
                    "target_qa_score": round(target_score, 3),
                    "near_threshold_tolerance": round(tolerance, 3),
                    "best_qa_score": round(best_score, 3),
                    "previous_attempts": _bounded_history(outcomes),
                    "previous_work_passes": work_pass_ledger[-3:],
                    "policy": (
                        "Repository work passes are independent from model self-improvement loops and Council review. "
                        "This pass must stay inside its work-scope budget. Security/build regressions are never tolerable."
                    ),
                }
                outcome: dict[str, Any] = {
                    "work_pass": work_pass,
                    "stage": stage,
                    "iteration": iteration,
                    "model": model,
                    "status": "running",
                    "accepted": False,
                    "accepted_under_near_threshold_tolerance": False,
                    # Backwards compatibility for persisted consumers.
                    "accepted_under_5_percent_rule": False,
                }
                try:
                    model_payload, model_used = await _run_model(
                        settings,
                        repository_id,
                        pass_intelligence,
                        context_files,
                        model=model,
                        orchestration=orchestration_context,
                    )
                    outcome["model"] = model_used or model
                    changes = _validated_changes(
                        model_payload,
                        max_change_files=int(work_scope["effective_file_limit"]),
                    )
                    outcome["proposed_change_count"] = len(changes)
                    if not changes:
                        outcome.update(status="no_safe_changes", reason="model_returned_empty_change_set")
                        outcomes.append(outcome)
                        shutil.rmtree(attempt_root, ignore_errors=True)
                        return False

                    _apply_changes(attempt_root, changes)
                    attempt_changed, attempt_deleted = _workspace_diff(staging, attempt_root)
                    if not attempt_changed and not attempt_deleted:
                        outcome.update(status="no_effect", reason="model_changes_did_not_modify_workspace")
                        outcomes.append(outcome)
                        shutil.rmtree(attempt_root, ignore_errors=True)
                        return False

                    pass_changed, pass_deleted = _workspace_diff(pass_baseline, attempt_root)
                    pass_change_count = len(set(pass_changed) | set(pass_deleted))
                    if pass_change_count > int(work_scope["effective_file_limit"]):
                        raise RepositoryImprovementError(
                            f"Work pass {work_pass} would modify {pass_change_count} files; "
                            f"maximum is {work_scope['effective_file_limit']}"
                        )

                    validation = _validate_candidate(
                        repository_id=repository_id,
                        root=attempt_root,
                        record=record,
                        intelligence=intelligence,
                        target_score=target_score,
                        tolerance=tolerance,
                    )
                    candidate_score = float(validation["score"])
                    outcome.update(
                        status="validated",
                        qa_score=candidate_score,
                        score_delta=round(candidate_score - pass_start_score, 3),
                        hard_blockers=list(validation["hard_blockers"]),
                        new_warning_checks=list(validation["new_warning_checks"]),
                        changed_files=attempt_changed,
                        deleted_files=attempt_deleted,
                        work_scope=_work_scope_usage(work_scope, pass_changed, pass_deleted),
                        meets_target=bool(validation["meets_target"]),
                        within_tolerance=bool(validation["within_tolerance"]),
                    )

                    promotable = bool(validation["eligible"]) and candidate_score + 0.001 >= best_score
                    if promotable:
                        shutil.rmtree(staging, ignore_errors=True)
                        shutil.move(str(attempt_root), str(staging))
                        best_score = candidate_score
                        selected_payload = model_payload
                        selected_model = str(model_used or model)
                        selected_validation = validation
                        pass_selected_validation = validation
                        outcome["promoted"] = True
                    else:
                        outcome["promoted"] = False
                        shutil.rmtree(attempt_root, ignore_errors=True)

                    accepted = promotable and bool(validation["meets_target"])
                    accepted_by_tolerance = (
                        promotable
                        and allow_near_threshold
                        and bool(validation["within_tolerance"])
                    )
                    if accepted or accepted_by_tolerance:
                        accepted_stage = stage
                        accepted_iteration = iteration
                        accepted_work_pass = work_pass
                        accepted_under_tolerance = accepted_by_tolerance
                        outcome["accepted"] = True
                        outcome["accepted_under_near_threshold_tolerance"] = accepted_by_tolerance
                        outcome["accepted_under_5_percent_rule"] = accepted_by_tolerance
                        outcome["status"] = (
                            "accepted_near_threshold" if accepted_by_tolerance else "accepted_target_met"
                        )
                        outcomes.append(outcome)
                        return True

                    outcome["status"] = (
                        "promoted_below_target" if promotable else "rejected_by_validation"
                    )
                    outcomes.append(outcome)
                    return False
                except Exception as exc:  # noqa: BLE001
                    outcome.update(status="attempt_failed", error=str(exc))
                    outcomes.append(outcome)
                    shutil.rmtree(attempt_root, ignore_errors=True)
                    return False

            for iteration, model in enumerate(loop_models, start=1):
                if await run_attempt(
                    stage="self_improvement",
                    iteration=iteration,
                    model=model,
                    allow_near_threshold=False,
                ):
                    break

            if accepted_stage is None:
                for iteration, model in enumerate(council_models, start=1):
                    if await run_attempt(
                        stage="council",
                        iteration=iteration,
                        model=model,
                        allow_near_threshold=True,
                    ):
                        break

            pass_changed, pass_deleted = _workspace_diff(pass_baseline, staging)
            pass_attempts = outcomes[pass_outcomes_start:]
            pass_scope_usage = _work_scope_usage(work_scope, pass_changed, pass_deleted)
            pass_ledger = {
                "work_pass": work_pass,
                "status": "accepted" if accepted_work_pass == work_pass else ("progressed" if pass_changed or pass_deleted else "blocked"),
                "changed_files": pass_changed,
                "deleted_files": pass_deleted,
                "work_scope": pass_scope_usage,
                "models_used": [str(item.get("model")) for item in pass_attempts if item.get("model")],
                "attempts": pass_attempts,
                "qa_result": pass_selected_validation.get("qa") if pass_selected_validation else None,
                "security_result": pass_selected_validation.get("security_validation") if pass_selected_validation else None,
            }
            work_pass_ledger.append(pass_ledger)
            shutil.rmtree(pass_baseline, ignore_errors=True)
            _set_job(
                settings,
                job_id,
                progress={
                    "execution_mode": normalised_mode,
                    "completed_work_passes": work_pass,
                    "max_work_passes": total_work_passes,
                    "accepted": accepted_work_pass is not None,
                },
                work_pass_ledger=work_pass_ledger,
            )

            if accepted_work_pass is not None:
                break
            if normalised_mode == "single_pass":
                break
            if not pass_changed and not pass_deleted:
                break

        if accepted_stage is None or selected_payload is None or selected_validation is None:
            tolerance_percent = round(tolerance * 100, 2)
            raise RepositoryImprovementError(
                "Repository improvement exhausted the configured work passes, self-improvement loops and bounded "
                f"Council review without meeting the quality threshold or its permitted {tolerance_percent}% "
                "near-threshold tolerance."
            )

        changed, deleted = _workspace_diff(record.workdir, staging)
        if not changed and not deleted:
            raise RepositoryImprovementError(
                "Accepted repository improvement produced no repository modifications"
            )

        qa_after = cast(dict[str, Any], selected_validation["qa"])
        security_validation = cast(
            dict[str, Any], selected_validation["security_validation"]
        )
        summary = str(
            selected_payload.get("summary")
            or f"Automated improvements for {repository_id}"
        )
        raw_risks = selected_payload.get("remaining_risks")
        remaining_risks = (
            [str(item) for item in raw_risks] if isinstance(raw_risks, list) else []
        )
        native_ci_risk = (
            "HIVE performs static repository validation on the generated copy but does not install dependencies "
            "or execute the repository's native CI/build/test suite. Run the repository's normal CI before deployment."
        )
        if native_ci_risk not in remaining_risks:
            remaining_risks.append(native_ci_risk)
        if accepted_under_tolerance:
            tolerance_percent = round(tolerance * 100, 2)
            monthly_audit_risk = (
                f"Accepted under the configured {tolerance_percent:g}% near-threshold tolerance after Council review; "
                "minor non-blocking issues may remain and should be revisited by the monthly audit."
            )
            if monthly_audit_risk not in remaining_risks:
                remaining_risks.append(monthly_audit_risk)

        raw_remaining_findings = selected_payload.get("remaining_findings")
        remaining_findings = (
            [str(item) for item in raw_remaining_findings]
            if isinstance(raw_remaining_findings, list)
            else []
        )
        cumulative_scope = _work_scope_usage(
            _work_scope_budget(settings, record.workdir, intelligence),
            changed,
            deleted,
        )

        self_improvement_outcomes = [
            item for item in outcomes if item.get("stage") == "self_improvement"
        ]
        council_outcomes = [item for item in outcomes if item.get("stage") == "council"]
        orchestration_report = {
            "protocol": "work_passes_loop_first_bounded_council_v2",
            "execution_mode": normalised_mode,
            "max_work_passes": total_work_passes,
            "work_passes_run": len(work_pass_ledger),
            "work_pass_ledger": work_pass_ledger,
            "accepted_work_pass": accepted_work_pass,
            "target_qa_score": round(target_score, 3),
            "near_threshold_tolerance": round(tolerance, 3),
            "self_improvement_max_loops": settings.repository_improvement_max_loops,
            "self_improvement_loops_run": len(self_improvement_outcomes),
            "self_improvement_loops": self_improvement_outcomes,
            "council_max_runs": settings.repository_improvement_max_council_runs,
            "council_runs": council_outcomes,
            "accepted_stage": accepted_stage,
            "accepted_iteration": accepted_iteration,
            "accepted_under_near_threshold_tolerance": accepted_under_tolerance,
            "accepted_under_5_percent_rule": accepted_under_tolerance,
            "final_qa_score": qa_after.get("score"),
        }
        report = {
            "job_id": job_id,
            "repository_id": repository_id,
            "source_fingerprint": record.manifest.fingerprint,
            "model_used": selected_model,
            "summary": summary,
            "changed_files": changed,
            "deleted_files": deleted,
            "cumulative_change_count": len(set(changed) | set(deleted)),
            "cumulative_work_scope": cumulative_scope,
            "work_pass_ledger": work_pass_ledger,
            "remaining_findings": remaining_findings,
            "remaining_external_ci_verification": [native_ci_risk],
            "remaining_risks": remaining_risks,
            "static_validation": qa_after,
            "security_validation": security_validation,
            "orchestration": orchestration_report,
            "generated_at": _now_iso(),
        }

        changed_zip = job_root / f"{repository_id}-improved-files.zip"
        full_zip = job_root / f"{repository_id}-improved-repository.zip"
        _zip_changed_files(staging, changed_zip, changed, deleted, report)
        _zip_tree(staging, full_zip)

        _set_job(settings, job_id, stage="persisting_artifacts", orchestration=orchestration_report)
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
                    "model_used": selected_model,
                    "changed_files": changed,
                    "deleted_files": deleted,
                    "remaining_risks": remaining_risks,
                    "qa_score_after": qa_after.get("score"),
                    "cumulative_work_scope": cumulative_scope,
                    "work_pass_ledger": work_pass_ledger,
                    "orchestration": orchestration_report,
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
            model_used=selected_model,
            summary=summary,
            changed_files=changed,
            deleted_files=deleted,
            change_count=len(changed) + len(deleted),
            remaining_risks=remaining_risks,
            qa_score_after=qa_after.get("score"),
            accepted_under_near_threshold_tolerance=accepted_under_tolerance,
            accepted_under_5_percent_rule=accepted_under_tolerance,
            cumulative_work_scope=cumulative_scope,
            work_pass_ledger=work_pass_ledger,
            remaining_findings=remaining_findings,
            remaining_external_ci_verification=[native_ci_risk],
            orchestration=orchestration_report,
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
    except asyncio.CancelledError:
        _set_job(
            settings,
            job_id,
            status="cancelled",
            stage="cancelled",
            finished_at=_now_iso(),
            ok=False,
            error="Repository improvement cancelled by operator.",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Repository improvement failed repository_id=%s job_id=%s", repository_id, job_id
        )
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
        # Remove every transient candidate workspace. In development/test the
        # ZIP artifacts remain available for download; production keeps only R2.
        for path in job_root.glob("*workspace"):
            shutil.rmtree(path, ignore_errors=True)
        if settings.production_require_r2:
            shutil.rmtree(job_root, ignore_errors=True)

def start_improvement_job(
    settings: Settings,
    repository_id: str,
    *,
    execution_mode: str = "single_pass",
    max_work_passes: int | None = None,
) -> dict[str, Any]:
    if not settings.repository_manager_enabled:
        raise RepositoryImprovementError("Repository Manager is disabled")
    if not settings.openrouter_api_key.strip():
        raise RepositoryImprovementError(
            "OPENROUTER_API_KEY is required for automatic repository improvements"
        )

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

    normalised_mode = execution_mode.strip().lower()
    if normalised_mode not in {"single_pass", "multi_pass"}:
        raise RepositoryImprovementError("execution_mode must be single_pass or multi_pass")
    configured_cap = int(settings.repository_improvement_max_work_passes)
    requested_passes = 1 if normalised_mode == "single_pass" else int(max_work_passes or configured_cap)
    if requested_passes < 1 or requested_passes > configured_cap:
        raise RepositoryImprovementError(
            f"max_work_passes must be between 1 and {configured_cap}"
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
        execution_mode=normalised_mode,
        max_work_passes=requested_passes,
        progress={"completed_work_passes": 0, "max_work_passes": requested_passes},
        ok=None,
    )
    task = asyncio.create_task(
        _run_job(
            settings,
            job_id,
            repository_id,
            execution_mode=normalised_mode,
            max_work_passes=requested_passes,
        ),
        name=f"repository-improvement-{job_id}",
    )
    with _JOB_LOCK:
        _TASKS[job_id] = task

    def forget_task(_task: asyncio.Task[None]) -> None:
        with _JOB_LOCK:
            _TASKS.pop(job_id, None)

    task.add_done_callback(forget_task)
    return payload


def cancel_improvement_job(
    settings: Settings,
    repository_id: str,
    job_id: str,
) -> dict[str, Any]:
    job = get_improvement_job(settings, repository_id, job_id)
    if job is None:
        raise RepositoryImprovementError("Unknown repository improvement job")
    if str(job.get("status")) in _TERMINAL_STATUSES:
        return job
    with _JOB_LOCK:
        task = _TASKS.get(job_id)
        if task is not None and not task.done():
            task.cancel()
    return _set_job(
        settings,
        job_id,
        status="cancelled",
        stage="cancelled",
        finished_at=_now_iso(),
        ok=False,
        error="Repository improvement cancelled by operator.",
    )



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

    local_artifacts = (
        job.get("local_artifacts") if isinstance(job.get("local_artifacts"), dict) else {}
    )
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
                max_bytes=max(
                    int(settings.repository_max_uncompressed_bytes), int(settings.max_upload_bytes)
                ),
                bucket=settings.r2_bucket_repositories,
                read_only=read_only,
            )
            return filename, obj.content, "application/zip"
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RepositoryImprovementError("Improvement artifact download failed: " + "; ".join(errors))

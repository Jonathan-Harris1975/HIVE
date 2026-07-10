from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.config import Settings
from app.ingestion.zip_ingestion import UnsafeZipError, extract_zip_safely

# Phase 1 - Repository Intelligence.
#
# RepositoryManager owns the lifecycle of a repository submitted to HIVE for
# analysis: safe extraction into a temporary working directory, fingerprinting,
# manifest generation (language + dependency detection), incremental
# re-indexing on subsequent uploads of the same repository, an in-process
# registry, and automatic cleanup of temporary extraction directories.
#
# Extracted repositories are never permanent: they live under a per-process
# temp root and are removed by TTL-based cleanup or explicit deletion. Any
# durable artefact (manifest, fingerprint, registry metadata) is small JSON
# that can be persisted separately (SQL/D1) by callers; this module only
# manages the working copy on local disk.

_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[str, "RepositoryRecord"] = {}

_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".php": "PHP",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".cs": "C#",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".md": "Markdown",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".vue": "Vue",
}

# Dependency manifest file -> declared ecosystem. Detection is intentionally
# shallow (file presence + best-effort parse); depth belongs to Phase 2
# (Repository Memory) and Phase 7 (Repository QA), not the manager itself.
_DEPENDENCY_MANIFESTS: dict[str, str] = {
    "requirements.txt": "pip",
    "requirements.in": "pip",
    "pyproject.toml": "python",
    "Pipfile": "pipenv",
    "package.json": "npm",
    "go.mod": "go",
    "Cargo.toml": "cargo",
    "composer.json": "composer",
    "Gemfile": "bundler",
}

_IGNORED_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
}


class RepositoryManagerError(ValueError):
    pass


class RepositoryWorkdirUnavailableError(RepositoryManagerError):
    """Raised when an operation needs a local working copy that no longer exists.

    This happens for repositories rehydrated from R2 manifests after a process
    restart (see rehydrate_registry_from_r2 below): the manifest metadata is
    available, but the extracted temp directory was never restored, so any
    operation that reads files from disk (reindex, diff) cannot proceed until
    the repository is re-uploaded.
    """


@dataclass(frozen=True)
class RepositoryFileEntry:
    path: str
    size_bytes: int
    sha256: str
    language: str | None


@dataclass(frozen=True)
class DependencyFinding:
    manifest_path: str
    ecosystem: str
    declared: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepositoryManifest:
    repository_id: str
    source_filename: str
    fingerprint: str
    file_count: int
    total_bytes: int
    languages: dict[str, int]
    dependencies: list[DependencyFinding]
    created_at: float
    updated_at: float
    indexed_version: int

    def public_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["dependencies"] = [asdict(item) for item in self.dependencies]
        return payload


@dataclass
class RepositoryRecord:
    repository_id: str
    workdir: Path
    manifest: RepositoryManifest
    files_index: dict[str, str]  # relative path -> sha256, used for incremental reindex
    last_accessed_at: float


@dataclass(frozen=True)
class RepositorySummary:
    repository_id: str
    source_filename: str
    fingerprint: str
    file_count: int
    total_bytes: int
    created_at: float
    updated_at: float
    indexed_version: int
    rehydrated: bool = False


def _repository_temp_root(settings: Settings) -> Path:
    root = Path(getattr(settings, "repository_temp_dir", "") or tempfile.gettempdir()) / "hive-repositories"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_repository_files(workdir: Path):
    for candidate in sorted(workdir.rglob("*")):
        if candidate.is_dir():
            continue
        if any(part in _IGNORED_DIR_NAMES for part in candidate.relative_to(workdir).parts):
            continue
        yield candidate


def _detect_language(path: Path) -> str | None:
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def _parse_requirements_txt(text: str) -> list[str]:
    declared = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[; ]", stripped, maxsplit=1)[0].strip()
        if name:
            declared.append(name)
    return declared


def _parse_package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    declared: list[str] = []
    for section in ("dependencies", "devDependencies"):
        declared.extend(sorted((data.get(section) or {}).keys()))
    return declared


def _scan_dependencies(workdir: Path) -> list[DependencyFinding]:
    findings: list[DependencyFinding] = []
    for candidate in _iter_repository_files(workdir):
        manifest_name = candidate.name
        ecosystem = _DEPENDENCY_MANIFESTS.get(manifest_name)
        if not ecosystem:
            continue
        relative = str(candidate.relative_to(workdir))
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            findings.append(DependencyFinding(manifest_path=relative, ecosystem=ecosystem, declared=[]))
            continue
        if manifest_name in {"requirements.txt", "requirements.in"}:
            declared = _parse_requirements_txt(text)
        elif manifest_name == "package.json":
            declared = _parse_package_json(text)
        else:
            # Presence-only detection for manifests not worth parsing here
            # (pyproject.toml, Cargo.toml, go.mod, composer.json, Gemfile, Pipfile).
            declared = []
        findings.append(DependencyFinding(manifest_path=relative, ecosystem=ecosystem, declared=declared))
    return findings


def _build_file_index(workdir: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    for candidate in _iter_repository_files(workdir):
        relative = str(candidate.relative_to(workdir))
        index[relative] = _sha256_file(candidate)
    return index


def _fingerprint_from_index(files_index: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(files_index):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files_index[relative_path].encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _language_counts(workdir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in _iter_repository_files(workdir):
        language = _detect_language(candidate)
        if language:
            counts[language] = counts.get(language, 0) + 1
    return counts


def register_repository(
    zip_bytes: bytes,
    *,
    settings: Settings,
    source_filename: str,
    max_files: int = 20_000,
    max_uncompressed_bytes: int = 512 * 1024 * 1024,
) -> RepositoryManifest:
    """Extract an uploaded repository ZIP and register it in the repository registry.

    Extraction is always into a fresh temporary directory scoped to this
    repository_id; nothing is written permanently to local disk.
    """
    repository_id = uuid.uuid4().hex
    root = _repository_temp_root(settings)
    workdir = root / repository_id
    workdir.mkdir(parents=True, exist_ok=False)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
        tmp_zip.write(zip_bytes)
        tmp_zip_path = Path(tmp_zip.name)

    try:
        extract_zip_safely(
            tmp_zip_path,
            workdir,
            max_files=max_files,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    except UnsafeZipError as error:
        shutil.rmtree(workdir, ignore_errors=True)
        raise RepositoryManagerError(str(error)) from error
    finally:
        tmp_zip_path.unlink(missing_ok=True)

    files_index = _build_file_index(workdir)
    fingerprint = _fingerprint_from_index(files_index)
    total_bytes = sum(path.stat().st_size for path in _iter_repository_files(workdir))
    now = time.time()

    manifest = RepositoryManifest(
        repository_id=repository_id,
        source_filename=source_filename,
        fingerprint=fingerprint,
        file_count=len(files_index),
        total_bytes=total_bytes,
        languages=_language_counts(workdir),
        dependencies=_scan_dependencies(workdir),
        created_at=now,
        updated_at=now,
        indexed_version=1,
    )

    record = RepositoryRecord(
        repository_id=repository_id,
        workdir=workdir,
        manifest=manifest,
        files_index=files_index,
        last_accessed_at=now,
    )
    with _REGISTRY_LOCK:
        _REGISTRY[repository_id] = record
    return manifest


def get_repository(repository_id: str) -> RepositoryRecord | None:
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(repository_id)
        if record is not None:
            record.last_accessed_at = time.time()
        return record


def list_repositories() -> list[RepositorySummary]:
    with _REGISTRY_LOCK:
        records = list(_REGISTRY.values())
    return [
        RepositorySummary(
            repository_id=record.manifest.repository_id,
            source_filename=record.manifest.source_filename,
            fingerprint=record.manifest.fingerprint,
            file_count=record.manifest.file_count,
            total_bytes=record.manifest.total_bytes,
            created_at=record.manifest.created_at,
            updated_at=record.manifest.updated_at,
            indexed_version=record.manifest.indexed_version,
            rehydrated=is_rehydrated(record),
        )
        for record in sorted(records, key=lambda item: item.manifest.updated_at, reverse=True)
    ]


def reindex_repository(repository_id: str) -> RepositoryManifest:
    """Incrementally re-index a previously registered repository's working copy.

    Only files that changed, were added, or were removed since the last index
    are reflected in the returned changed-file counts; the manifest itself is
    still a full current snapshot.
    """
    with _REGISTRY_LOCK:
        record = _REGISTRY.get(repository_id)
        if record is None:
            raise RepositoryManagerError(f"Unknown repository_id: {repository_id}")
        if is_rehydrated(record):
            raise RepositoryWorkdirUnavailableError(
                f"Repository {repository_id} was rehydrated from R2 after a restart and has "
                "no local working copy. Re-upload the repository to enable reindexing."
            )

    new_index = _build_file_index(record.workdir)
    old_index = record.files_index
    added = sorted(set(new_index) - set(old_index))
    removed = sorted(set(old_index) - set(new_index))
    changed = sorted(
        path for path in (set(new_index) & set(old_index)) if new_index[path] != old_index[path]
    )

    fingerprint = _fingerprint_from_index(new_index)
    total_bytes = sum(path.stat().st_size for path in _iter_repository_files(record.workdir))
    now = time.time()
    version = record.manifest.indexed_version + (1 if (added or removed or changed) else 0)

    manifest = RepositoryManifest(
        repository_id=record.manifest.repository_id,
        source_filename=record.manifest.source_filename,
        fingerprint=fingerprint,
        file_count=len(new_index),
        total_bytes=total_bytes,
        languages=_language_counts(record.workdir),
        dependencies=_scan_dependencies(record.workdir),
        created_at=record.manifest.created_at,
        updated_at=now,
        indexed_version=version,
    )

    with _REGISTRY_LOCK:
        record.manifest = manifest
        record.files_index = new_index
        record.last_accessed_at = now

    return manifest


def repository_diff(repository_id: str) -> dict[str, list[str]] | None:
    """Preview added/removed/changed files without mutating the registry."""
    record = get_repository(repository_id)
    if record is None:
        return None
    if is_rehydrated(record):
        raise RepositoryWorkdirUnavailableError(
            f"Repository {repository_id} was rehydrated from R2 after a restart and has "
            "no local working copy. Re-upload the repository to preview changes."
        )
    new_index = _build_file_index(record.workdir)
    old_index = record.files_index
    return {
        "added": sorted(set(new_index) - set(old_index)),
        "removed": sorted(set(old_index) - set(new_index)),
        "changed": sorted(
            path for path in (set(new_index) & set(old_index)) if new_index[path] != old_index[path]
        ),
    }


def cleanup_repository(repository_id: str) -> bool:
    with _REGISTRY_LOCK:
        record = _REGISTRY.pop(repository_id, None)
    if record is None:
        return False
    shutil.rmtree(record.workdir, ignore_errors=True)
    return True


def cleanup_expired_repositories(*, ttl_seconds: int) -> list[str]:
    """Remove registry entries (and their temp directories) idle longer than ttl_seconds."""
    cutoff = time.time() - ttl_seconds
    with _REGISTRY_LOCK:
        expired = [
            repository_id
            for repository_id, record in _REGISTRY.items()
            if record.last_accessed_at < cutoff
        ]
    for repository_id in expired:
        cleanup_repository(repository_id)
    return expired


def registry_size() -> int:
    with _REGISTRY_LOCK:
        return len(_REGISTRY)


# ---------------------------------------------------------------------------
# Startup rehydration (RC1 fix — Audit Finding #1)
# ---------------------------------------------------------------------------
# On process startup the in-memory _REGISTRY is empty even though manifest
# JSON objects persist in R2 under `manifests/{repository_id}.json`.  This
# function is called from main.py's lifespan (mirrors model_registry's
# load_registry_from_store pattern) and rebuilds lightweight RepositoryRecord
# stubs from every manifest found in R2.
#
# Because extracted working directories (workdir) are ephemeral temp files
# that do NOT survive restarts, rehydrated records have no valid workdir.
# Operations that require the workdir (reindex, QA, Council) will detect the
# missing directory and return a clear RepositoryManagerError rather than
# crashing silently.  The manifest metadata (fingerprint, languages, file
# count, etc.) is immediately available for listing and dashboard display.

_TOMBSTONE_DIR = Path("/dev/null")  # sentinel — workdir absent after rehydration


def is_rehydrated(record: RepositoryRecord) -> bool:
    """True if `record` has no local working copy (rehydrated from R2 after a restart)."""
    return record.workdir == _TOMBSTONE_DIR


def rehydrate_registry_from_r2(settings: "Settings") -> int:  # noqa: F821 (forward ref OK)
    """Load repository manifests from R2 and rebuild the in-memory registry.

    Called once during HIVE startup.  Returns the number of manifests
    successfully rehydrated.  Failures are logged individually and never
    prevent startup.
    """
    import json
    import logging

    logger = logging.getLogger("uvicorn.error.hive.repository_manager")

    try:
        from app.storage.r2 import R2Storage

        r2 = R2Storage(settings)
        if not r2.enabled:
            logger.info("Repository rehydration skipped — R2 not configured")
            return 0

        keys = r2.list_objects(
            prefix="manifests/",
            limit=5_000,
            bucket="hive-repositories",
            read_only=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Repository rehydration aborted — R2 list failed: %s", exc)
        return 0

    rehydrated = 0
    for obj in keys:
        key = obj.key
        if not key.endswith(".json"):
            continue
        try:
            raw = r2.read_object(
                key,
                max_bytes=2 * 1024 * 1024,  # 2 MB cap per manifest
                bucket="hive-repositories",
                read_only=True,
            )
            data: dict = json.loads(raw.content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository rehydration skipped key=%s error=%s", key, exc)
            continue

        repository_id = data.get("repository_id")
        if not repository_id:
            continue

        with _REGISTRY_LOCK:
            if repository_id in _REGISTRY:
                # Already registered (e.g. uploaded in this process run) — skip.
                continue

        try:
            deps = [
                DependencyFinding(
                    manifest_path=d["manifest_path"],
                    ecosystem=d["ecosystem"],
                    declared=list(d.get("declared") or []),
                )
                for d in (data.get("dependencies") or [])
            ]
            manifest = RepositoryManifest(
                repository_id=repository_id,
                source_filename=data.get("source_filename", "unknown"),
                fingerprint=data.get("fingerprint", ""),
                file_count=int(data.get("file_count", 0)),
                total_bytes=int(data.get("total_bytes", 0)),
                languages=dict(data.get("languages") or {}),
                dependencies=deps,
                created_at=float(data.get("created_at", 0)),
                updated_at=float(data.get("updated_at", 0)),
                indexed_version=int(data.get("indexed_version", 1)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Repository rehydration manifest parse failed key=%s error=%s", key, exc
            )
            continue

        record = RepositoryRecord(
            repository_id=repository_id,
            workdir=_TOMBSTONE_DIR,
            manifest=manifest,
            files_index={},
            last_accessed_at=float(data.get("updated_at", time.time())),
        )
        with _REGISTRY_LOCK:
            _REGISTRY[repository_id] = record
        rehydrated += 1
        logger.debug("Repository rehydrated repository_id=%s", repository_id)

    logger.info("Repository registry rehydrated from R2 count=%d", rehydrated)
    return rehydrated

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import tomllib
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
# Extracted working copies live under a per-process temp root. The repository
# API persists both the source ZIP and its manifest in R2, so startup can
# restore a validated local working copy after a process restart. This module
# owns local extraction, stable repository identity and registry lifecycle;
# durable object storage remains the API/storage layer's responsibility.

_REGISTRY_LOCK = threading.Lock()
_REGISTRY: dict[str, "RepositoryRecord"] = {}

_GOVERNED_REPOSITORY_IDS = {
    "hive": "HIVE",
    "hive-ui": "HIVE-UI",
    "aims": "AIMS",
    "aims-ui": "AIMS-UI",
    "rams": "RAMS",
    "mast": "MAST",
    "irs": "IRS",
    "website": "Website",
    "jonathan-harris-website": "Website",
    "shared": "Shared",
}

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


def repository_id_from_filename(source_filename: str) -> str:
    """Return a stable repository id from an uploaded archive filename.

    GitHub archives are normally named ``<repo>-main.zip`` or
    ``<repo>-master.zip``.  Repository Intelligence and Repository Memory need
    a stable identifier across monthly uploads and process restarts, so random
    UUIDs are not suitable here.
    """
    filename = Path(source_filename or "repository.zip").name
    stem = re.sub(r"(?i)\.zip$", "", filename).strip()
    stem = re.sub(r"(?i)-(?:main|master)$", "", stem).strip()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    if not slug:
        raise RepositoryManagerError("Repository filename does not contain a usable repository id")
    return _GOVERNED_REPOSITORY_IDS.get(slug.lower(), slug)


def _collapse_single_root_directory(workdir: Path) -> str | None:
    """Flatten a single archive wrapper directory and return its name.

    Browser-generated GitHub archives normally use ``<repo>-main`` while the
    GitHub zipball API uses an owner/repository/commit-derived wrapper. Both
    shapes must expose the repository root to QA and profile detection.
    """
    children = list(workdir.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        return None
    wrapper = children[0]
    wrapper_name = wrapper.name
    for child in list(wrapper.iterdir()):
        shutil.move(str(child), str(workdir / child.name))
    wrapper.rmdir()
    return wrapper_name


def _extract_repository_archive(
    archive_path: Path,
    workdir: Path,
    *,
    max_files: int,
    max_uncompressed_bytes: int,
) -> str | None:
    extract_zip_safely(
        archive_path,
        workdir,
        max_files=max_files,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )
    return _collapse_single_root_directory(workdir)


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


def _normalise_python_dependency(requirement: str) -> str:
    name = re.split(r"[<>=!~\[; ]", requirement.strip(), maxsplit=1)[0].strip()
    return name


def _parse_pyproject_toml(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []

    declared: set[str] = set()
    project = data.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            for requirement in dependencies:
                if isinstance(requirement, str):
                    name = _normalise_python_dependency(requirement)
                    if name:
                        declared.add(name)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for requirements in optional.values():
                if not isinstance(requirements, list):
                    continue
                for requirement in requirements:
                    if isinstance(requirement, str):
                        name = _normalise_python_dependency(requirement)
                        if name:
                            declared.add(name)

    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    poetry_dependencies = poetry.get("dependencies") if isinstance(poetry, dict) else None
    if isinstance(poetry_dependencies, dict):
        for name in poetry_dependencies:
            if str(name).lower() != "python":
                declared.add(str(name))

    return sorted(declared, key=str.lower)


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
        elif manifest_name == "pyproject.toml":
            declared = _parse_pyproject_toml(text)
        else:
            # Presence-only detection for manifests not worth parsing here
            # (Cargo.toml, go.mod, composer.json, Gemfile, Pipfile).
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
    root = _repository_temp_root(settings)
    staging_dir = root / f".upload-{uuid.uuid4().hex}.staging"
    staging_dir.mkdir(parents=True, exist_ok=False)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
        tmp_zip.write(zip_bytes)
        tmp_zip_path = Path(tmp_zip.name)

    try:
        wrapper_name = _extract_repository_archive(
            tmp_zip_path,
            staging_dir,
            max_files=max_files,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
    except UnsafeZipError as error:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise RepositoryManagerError(str(error)) from error
    finally:
        tmp_zip_path.unlink(missing_ok=True)

    # Prefer the repository name embedded by GitHub in the archive itself.
    # This keeps identity stable even if a browser or operator renames the ZIP.
    use_wrapper_identity = bool(
        wrapper_name and wrapper_name.lower().endswith(("-main", "-master"))
    )
    identity_filename = f"{wrapper_name}.zip" if use_wrapper_identity else source_filename
    repository_id = repository_id_from_filename(identity_filename)

    files_index = _build_file_index(staging_dir)
    fingerprint = _fingerprint_from_index(files_index)
    total_bytes = sum(path.stat().st_size for path in _iter_repository_files(staging_dir))
    now = time.time()

    with _REGISTRY_LOCK:
        previous = _REGISTRY.get(repository_id)

    created_at = previous.manifest.created_at if previous is not None else now
    previous_version = previous.manifest.indexed_version if previous is not None else 0
    previous_fingerprint = previous.manifest.fingerprint if previous is not None else None
    indexed_version = previous_version if previous_fingerprint == fingerprint else previous_version + 1
    indexed_version = max(1, indexed_version)

    manifest = RepositoryManifest(
        repository_id=repository_id,
        source_filename=source_filename,
        fingerprint=fingerprint,
        file_count=len(files_index),
        total_bytes=total_bytes,
        languages=_language_counts(staging_dir),
        dependencies=_scan_dependencies(staging_dir),
        created_at=created_at,
        updated_at=now,
        indexed_version=indexed_version,
    )

    workdir = root / repository_id
    if previous is not None and not is_rehydrated(previous):
        shutil.rmtree(previous.workdir, ignore_errors=True)
    shutil.rmtree(workdir, ignore_errors=True)
    staging_dir.replace(workdir)

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


def restore_repository_snapshot(
    archive_path: Path,
    *,
    settings: Settings,
    manifest: RepositoryManifest,
) -> RepositoryRecord:
    """Restore a persisted repository ZIP into a usable local working copy.

    The archive is validated against the persisted manifest fingerprint before
    it becomes active.  A corrupt or stale R2 snapshot is therefore never
    silently treated as the registered repository.
    """
    root = _repository_temp_root(settings)
    staging_dir = root / f".{manifest.repository_id}-{uuid.uuid4().hex}.restore"
    staging_dir.mkdir(parents=True, exist_ok=False)
    try:
        _extract_repository_archive(
            archive_path,
            staging_dir,
            max_files=settings.repository_max_files,
            max_uncompressed_bytes=settings.repository_max_uncompressed_bytes,
        )
        files_index = _build_file_index(staging_dir)
        fingerprint = _fingerprint_from_index(files_index)
        if fingerprint != manifest.fingerprint:
            raise RepositoryManagerError(
                f"Persisted snapshot fingerprint mismatch for {manifest.repository_id}"
            )

        workdir = root / manifest.repository_id
        shutil.rmtree(workdir, ignore_errors=True)
        staging_dir.replace(workdir)
        return RepositoryRecord(
            repository_id=manifest.repository_id,
            workdir=workdir,
            manifest=manifest,
            files_index=files_index,
            last_accessed_at=time.time(),
        )
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


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
# On process startup the in-memory _REGISTRY is empty even though repository
# manifests and source snapshots persist in R2. This function is called from
# main.py's lifespan and rebuilds the registry, restoring each validated ZIP
# into a local working copy. Historical manifest-only registrations remain as
# explicit tombstones until they are uploaded once under the durable model.

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
        if not settings.r2_bucket_repositories or not (r2.write_enabled or r2.read_enabled):
            logger.info("Repository rehydration skipped — R2 not configured")
            return 0

        credential_modes: list[bool] = []
        if r2.read_enabled:
            credential_modes.append(True)
        if r2.write_enabled:
            credential_modes.append(False)

        keys = None
        list_errors: list[str] = []
        for read_only in credential_modes:
            try:
                keys = r2.list_objects(
                    prefix="manifests/",
                    limit=5_000,
                    bucket=settings.r2_bucket_repositories,
                    read_only=read_only,
                )
                break
            except Exception as exc:  # noqa: BLE001
                list_errors.append(f"{'read' if read_only else 'write'} credentials: {exc}")
        if keys is None:
            raise RuntimeError("; ".join(list_errors) or "no usable R2 credentials")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Repository rehydration aborted — R2 list failed: %s", exc)
        return 0

    # Historical builds created a new UUID manifest on every upload. Process
    # newest objects first so the latest snapshot wins when several legacy
    # manifests resolve to the same stable governed repository id.
    keys = sorted(keys, key=lambda item: getattr(item, "last_modified", None) or "", reverse=True)

    rehydrated = 0
    for obj in keys:
        key = obj.key
        if not key.endswith(".json"):
            continue
        raw = None
        read_errors: list[str] = []
        for read_only in credential_modes:
            try:
                raw = r2.read_object(
                    key,
                    max_bytes=2 * 1024 * 1024,  # 2 MB cap per manifest
                    bucket=settings.r2_bucket_repositories,
                    read_only=read_only,
                )
                break
            except Exception as exc:  # noqa: BLE001
                read_errors.append(f"{'read' if read_only else 'write'} credentials: {exc}")
        if raw is None:
            logger.warning(
                "Repository rehydration skipped key=%s error=%s",
                key,
                "; ".join(read_errors) or "manifest unavailable",
            )
            continue
        try:
            data: dict = json.loads(raw.content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repository rehydration skipped key=%s error=%s", key, exc)
            continue

        stored_repository_id = str(data.get("repository_id") or "").strip()
        if not stored_repository_id:
            continue

        source_filename = str(data.get("source_filename") or "unknown")
        derived_repository_id = repository_id_from_filename(source_filename)
        repository_id = (
            derived_repository_id
            if re.fullmatch(r"[0-9a-f]{32}", stored_repository_id, flags=re.IGNORECASE)
            else stored_repository_id
        )

        with _REGISTRY_LOCK:
            already_registered = repository_id in _REGISTRY
        if already_registered:
            # The newest manifest for this stable id has already won. Remove
            # obsolete UUID-era objects when write credentials are available.
            if stored_repository_id != repository_id and r2.write_enabled:
                try:
                    r2.delete_objects(
                        [key, f"snapshots/{stored_repository_id}.zip"],
                        bucket=settings.r2_bucket_repositories,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Stale repository object cleanup failed repository_id=%s key=%s error=%s",
                        repository_id,
                        key,
                        exc,
                    )
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
                source_filename=source_filename,
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

        if stored_repository_id != repository_id:
            # One-time migration from the historical random UUID id scheme to
            # stable governed repository ids (HIVE, HIVE-UI, AIMS, ...).
            try:
                from app.services.repository_memory import migrate_repository_memory_id
                from app.storage.d1 import D1MetadataStore

                d1_store = D1MetadataStore(settings)
                if d1_store.enabled:
                    migrate_repository_memory_id(
                        d1_store,
                        old_repository_id=stored_repository_id,
                        new_repository_id=repository_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Repository Memory id migration failed old=%s new=%s error=%s",
                    stored_repository_id,
                    repository_id,
                    exc,
                )

            if r2.write_enabled:
                try:
                    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                        tmp.write(json.dumps(manifest.public_payload()).encode("utf-8"))
                        canonical_manifest_path = Path(tmp.name)
                    try:
                        r2.put_file(
                            canonical_manifest_path,
                            f"manifests/{repository_id}.json",
                            content_type="application/json",
                            bucket=settings.r2_bucket_repositories,
                            public_base_url=None,
                        )
                        r2.delete_objects([key], bucket=settings.r2_bucket_repositories)
                    finally:
                        canonical_manifest_path.unlink(missing_ok=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Repository manifest id migration failed old=%s new=%s error=%s",
                        stored_repository_id,
                        repository_id,
                        exc,
                    )

        record: RepositoryRecord | None = None
        snapshot_ids = [repository_id]
        if stored_repository_id != repository_id:
            snapshot_ids.append(stored_repository_id)

        for snapshot_id in snapshot_ids:
            snapshot_key = f"snapshots/{snapshot_id}.zip"
            snapshot_errors: list[str] = []
            for read_only in credential_modes:
                archive_path: Path | None = None
                try:
                    stream = r2.open_object(
                        snapshot_key,
                        bucket=settings.r2_bucket_repositories,
                        max_bytes=settings.repository_max_uncompressed_bytes,
                        read_only=read_only,
                    )
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                            while True:
                                chunk = stream.body.read(1024 * 1024)
                                if not chunk:
                                    break
                                tmp.write(chunk)
                            archive_path = Path(tmp.name)
                    finally:
                        stream.body.close()
                    record = restore_repository_snapshot(
                        archive_path,
                        settings=settings,
                        manifest=manifest,
                    )
                    if snapshot_id != repository_id and r2.write_enabled:
                        r2.put_file(
                            archive_path,
                            f"snapshots/{repository_id}.zip",
                            content_type="application/zip",
                            bucket=settings.r2_bucket_repositories,
                            public_base_url=None,
                        )
                        r2.delete_objects(
                            [snapshot_key],
                            bucket=settings.r2_bucket_repositories,
                        )
                    logger.debug(
                        "Repository working copy restored repository_id=%s snapshot=%s",
                        repository_id,
                        snapshot_key,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    snapshot_errors.append(f"{'read' if read_only else 'write'} credentials: {exc}")
                finally:
                    if archive_path is not None:
                        archive_path.unlink(missing_ok=True)
            if record is not None:
                break
            logger.debug(
                "Repository snapshot restore unavailable repository_id=%s snapshot=%s error=%s",
                repository_id,
                snapshot_key,
                "; ".join(snapshot_errors) or "snapshot unavailable",
            )

        if record is None:
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

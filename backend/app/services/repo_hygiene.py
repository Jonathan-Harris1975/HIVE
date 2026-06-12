from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.core.version import BUILD_STAGE

DEFAULT_IGNORED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".cache",
}

ORPHAN_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".bak",
    ".orig",
    ".rej",
    ".swp",
    ".swo",
}

ORPHAN_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
}

GENERATED_ARCHIVE_SUFFIXES = {
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
}

MAX_HASH_BYTES = 2_000_000


@dataclass(frozen=True)
class RepoFile:
    path: str
    size_bytes: int
    suffix: str
    sha256: str | None = None


def default_repo_root() -> Path:
    """Resolve the repository root from this service module.

    backend/app/services/repo_hygiene.py -> repo root is three parents up.
    The result is intentionally filesystem local and read-only.
    """

    return Path(__file__).resolve().parents[3]


def repo_hygiene_report(
    *,
    repo_root: str | os.PathLike[str] | None = None,
    include_hashes: bool = True,
    max_files: int = 5000,
) -> dict[str, object]:
    """Return a safe duplicate/orphan hygiene report for the checked-out repo.

    v1.13 does not delete files. It returns a deletion manifest with cautious
    candidates that can be reviewed in the repo before any cleanup commit.
    """

    root = Path(repo_root).resolve() if repo_root else default_repo_root()
    files = list(_iter_repo_files(root, max_files=max_files))
    duplicate_content = _duplicate_content(files) if include_hashes else []
    duplicate_names = _duplicate_names(files)
    orphan_candidates = _orphan_candidates(files)
    generated_artifacts = _generated_artifacts(files)
    deletion_manifest = _deletion_manifest(orphan_candidates=orphan_candidates, generated_artifacts=generated_artifacts)

    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "repo_root": str(root),
        "scanned_file_count": len(files),
        "ignored_dirs": sorted(DEFAULT_IGNORED_DIRS),
        "duplicate_content_group_count": len(duplicate_content),
        "duplicate_name_group_count": len(duplicate_names),
        "orphan_candidate_count": len(orphan_candidates),
        "generated_artifact_count": len(generated_artifacts),
        "duplicate_content": duplicate_content,
        "duplicate_names": duplicate_names[:100],
        "orphan_candidates": orphan_candidates,
        "generated_artifacts": generated_artifacts,
        "deletion_manifest": deletion_manifest,
        "safety_note": "Report only. No deletion is performed by HIVE v1.13.",
    }


def _iter_repo_files(root: Path, *, max_files: int) -> Iterable[RepoFile]:
    seen = 0
    for path in root.rglob("*"):
        if seen >= max_files:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_ignored(rel):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        digest = _sha256_for_file(path) if stat.st_size <= MAX_HASH_BYTES else None
        seen += 1
        yield RepoFile(path=rel, size_bytes=stat.st_size, suffix=path.suffix.lower(), sha256=digest)


def _is_ignored(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in DEFAULT_IGNORED_DIRS for part in parts):
        return True
    if rel.startswith((".git/", ".venv/", "node_modules/")):
        return True
    return False


def _sha256_for_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _duplicate_content(files: list[RepoFile]) -> list[dict[str, object]]:
    by_hash: dict[str, list[RepoFile]] = defaultdict(list)
    for item in files:
        if item.sha256 and item.size_bytes > 0:
            by_hash[item.sha256].append(item)
    groups = []
    for digest, items in by_hash.items():
        if len(items) < 2:
            continue
        groups.append({
            "sha256": digest,
            "count": len(items),
            "size_bytes": items[0].size_bytes,
            "paths": [item.path for item in items],
            "review_note": "Duplicate content. Keep intentional examples/templates; delete only confirmed stale copies.",
        })
    groups.sort(key=lambda group: (-int(group["count"]), -int(group["size_bytes"])))
    return groups[:100]


def _duplicate_names(files: list[RepoFile]) -> list[dict[str, object]]:
    by_name: dict[str, list[RepoFile]] = defaultdict(list)
    for item in files:
        by_name[Path(item.path).name].append(item)
    groups = []
    for name, items in by_name.items():
        if len(items) < 2:
            continue
        groups.append({
            "name": name,
            "count": len(items),
            "paths": [item.path for item in items],
            "review_note": "Duplicate filename only; this is often legitimate in tests/docs.",
        })
    groups.sort(key=lambda group: (-int(group["count"]), str(group["name"])))
    return groups


def _orphan_candidates(files: list[RepoFile]) -> list[dict[str, object]]:
    candidates = []
    for item in files:
        name = Path(item.path).name
        if name in ORPHAN_FILE_NAMES or item.suffix in ORPHAN_FILE_SUFFIXES:
            candidates.append({
                "path": item.path,
                "size_bytes": item.size_bytes,
                "reason": "orphan_or_local_machine_artifact",
                "safe_to_delete_if_untracked": True,
            })
    return sorted(candidates, key=lambda item: str(item["path"]))


def _generated_artifacts(files: list[RepoFile]) -> list[dict[str, object]]:
    candidates = []
    for item in files:
        lower = item.path.lower()
        name = Path(item.path).name.lower()
        if item.suffix in GENERATED_ARCHIVE_SUFFIXES and ("patch" in name or "hive-main" in name or "bundle" in name):
            candidates.append({
                "path": item.path,
                "size_bytes": item.size_bytes,
                "reason": "generated_patch_or_release_artifact_inside_repo",
                "safe_to_delete_if_not_source_release": True,
            })
        elif lower.endswith(".patch"):
            candidates.append({
                "path": item.path,
                "size_bytes": item.size_bytes,
                "reason": "generated_unified_patch_inside_repo",
                "safe_to_delete_if_not_intended_source_doc": True,
            })
    return sorted(candidates, key=lambda item: str(item["path"]))


def _deletion_manifest(*, orphan_candidates: list[dict[str, object]], generated_artifacts: list[dict[str, object]]) -> dict[str, object]:
    recommended = [*orphan_candidates, *generated_artifacts]
    return {
        "dry_run": True,
        "recommended_delete_count": len(recommended),
        "recommended_delete_paths": [str(item["path"]) for item in recommended],
        "manual_review_required": True,
        "instructions": [
            "Review every path before deleting.",
            "Prefer git status before deletion.",
            "Do not delete examples/templates merely because filenames duplicate.",
            "Commit cleanup separately from feature changes.",
        ],
    }

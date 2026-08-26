from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.repository_manager import RepositoryRecord

_IGNORED_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
_ENV_TEMPLATE_NAMES = {".env.example", ".env.sample", ".env.template"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in _IGNORED_PARTS for part in path.relative_to(root).parts)
    ]


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _first_json(files: list[Path], name: str) -> tuple[Path, dict[str, Any]] | None:
    matches = sorted((path for path in files if path.name == name), key=lambda p: len(p.parts))
    for path in matches:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return path, parsed
    return None


def _environment_schema(root: Path, files: list[Path]) -> dict[str, Any]:
    source_files: list[str] = []
    variables: set[str] = set()
    for path in files:
        lower_name = path.name.lower()
        if lower_name not in _ENV_TEMPLATE_NAMES and not lower_name.endswith((".env.example", ".env.sample")):
            continue
        source_files.append(_relative(root, path))
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match:
                variables.add(match.group(1))
    return {
        "status": "detected" if source_files else "no_template_detected",
        "source_files": sorted(source_files),
        "variable_count": len(variables),
        "variables": sorted(variables),
        "generated_at": _now_iso(),
    }


def build_repository_memory_profile(record: RepositoryRecord) -> dict[str, Any]:
    root = record.workdir
    manifest = record.manifest
    files = _files(root)
    relative_paths = [_relative(root, path) for path in files]
    basenames = {path.name for path in files}
    lower_paths = [path.lower() for path in relative_paths]

    top_level_dirs = sorted(
        {
            relative.parts[0]
            for path in files
            if len((relative := path.relative_to(root)).parts) > 1
        }
    )[:30]
    dependency_ecosystems = sorted({dep.ecosystem for dep in manifest.dependencies})
    primary_languages = [
        {"language": language, "files": count}
        for language, count in sorted(manifest.languages.items(), key=lambda item: item[1], reverse=True)[:10]
    ]

    package = _first_json(files, "package.json")
    package_scripts: dict[str, str] = {}
    if package:
        scripts = package[1].get("scripts")
        if isinstance(scripts, dict):
            for key in ("build", "check", "lint", "typecheck", "test", "test:unit", "test:e2e", "deploy"):
                value = scripts.get(key)
                if isinstance(value, str):
                    package_scripts[key] = value

    standards_files = sorted(
        path
        for path in relative_paths
        if Path(path).name
        in {
            "pyproject.toml",
            "ruff.toml",
            ".ruff.toml",
            "mypy.ini",
            ".mypy.ini",
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.ts",
            ".eslintrc",
            ".eslintrc.json",
            ".prettierrc",
            ".prettierrc.json",
            "tsconfig.json",
            "biome.json",
        }
    )
    test_paths = sorted(
        path
        for path in relative_paths
        if "/tests/" in f"/{path.lower()}/"
        or Path(path).name.lower().startswith("test_")
        or ".test." in Path(path).name.lower()
        or Path(path).name.lower().endswith("_test.py")
    )[:100]

    deployment_signals: list[str] = []
    if any(Path(path).name.lower() == "dockerfile" for path in relative_paths):
        deployment_signals.append("Docker")
    if any(Path(path).name.lower().startswith("wrangler.") for path in relative_paths):
        deployment_signals.append("Cloudflare Workers")
    if any("koyeb" in path for path in lower_paths):
        deployment_signals.append("Koyeb")
    if "vercel.json" in {name.lower() for name in basenames}:
        deployment_signals.append("Vercel")
    if "netlify.toml" in {name.lower() for name in basenames}:
        deployment_signals.append("Netlify")
    if any(path.startswith(".github/workflows/") for path in relative_paths):
        deployment_signals.append("GitHub Actions")

    architecture_signals: list[str] = []
    if any(path.startswith(("backend/", "api/", "server/")) for path in lower_paths):
        architecture_signals.append("backend")
    if any(path.startswith(("frontend/", "src/", "web/")) for path in lower_paths) and "package.json" in basenames:
        architecture_signals.append("frontend")
    if any("worker" in path for path in lower_paths):
        architecture_signals.append("worker")
    if any("fastapi" in dep.declared for dep in manifest.dependencies):
        architecture_signals.append("FastAPI")
    if package and isinstance(package[1].get("dependencies"), dict):
        deps = package[1]["dependencies"]
        if "react" in deps:
            architecture_signals.append("React")

    return {
        "architecture_summary": {
            "repository_id": manifest.repository_id,
            "architecture_signals": sorted(set(architecture_signals)),
            "top_level_directories": top_level_dirs,
            "primary_languages": primary_languages,
            "dependency_ecosystems": dependency_ecosystems,
            "file_count": manifest.file_count,
            "generated_at": _now_iso(),
        },
        "coding_standards": {
            "status": "detected" if standards_files else "no_explicit_config_detected",
            "configuration_files": standards_files,
            "test_file_count": len(test_paths),
            "test_files_sample": test_paths[:20],
            "generated_at": _now_iso(),
        },
        "build_profile": {
            "dependency_manifests": [dep.manifest_path for dep in manifest.dependencies],
            "package_scripts": package_scripts,
            "has_dockerfile": any(Path(path).name.lower() == "dockerfile" for path in relative_paths),
            "generated_at": _now_iso(),
        },
        "deployment_profile": {
            "status": "detected" if deployment_signals else "no_explicit_target_detected",
            "targets": sorted(set(deployment_signals)),
            "workflow_files": sorted(path for path in relative_paths if path.startswith(".github/workflows/"))[:50],
            "generated_at": _now_iso(),
        },
        "environment_schema": _environment_schema(root, files),
    }

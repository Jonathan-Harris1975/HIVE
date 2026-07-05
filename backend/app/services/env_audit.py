from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.config import Settings

# Phase 13 - Environment.
#
# The programme's requirement is "store all infrastructure configuration in
# environment variables... do not hard-code infrastructure throughout the
# codebase." Phases 1-12 already did this as they went (every new setting
# added an AliasChoices env var name and a .env.example entry). This module
# is the audit: it introspects every Settings field's declared environment
# variable name(s) and cross-checks them against .env.example, so drift
# (a field added without a matching .env.example line, or vice versa) shows
# up as a finding instead of silently rotting.

_ENV_LINE_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=")


def _field_env_names(field_name: str, field_info: Any) -> list[str]:
    alias = getattr(field_info, "validation_alias", None)
    choices = getattr(alias, "choices", None)
    if choices:
        return [str(choice) for choice in choices if isinstance(choice, str)]
    if isinstance(alias, str):
        return [alias]
    return [field_name.upper()]


def _parse_env_example(path: Path) -> set[str]:
    if not path.exists():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_PATTERN.match(stripped)
        if match:
            names.add(match.group(1))
    return names


def _default_env_example_path() -> Path:
    candidates = [
        Path(".env.example"),
        Path(__file__).resolve().parents[3] / ".env.example",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def audit_environment(*, env_example_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(env_example_path) if env_example_path else _default_env_example_path()
    documented_names = _parse_env_example(path)

    undocumented_fields: list[dict[str, Any]] = []
    documented_field_count = 0
    all_declared_names: set[str] = set()

    for field_name, field_info in Settings.model_fields.items():
        env_names = _field_env_names(field_name, field_info)
        all_declared_names.update(env_names)
        if any(name in documented_names for name in env_names):
            documented_field_count += 1
        else:
            undocumented_fields.append({"field": field_name, "expected_env_names": env_names})

    extra_in_env_example = sorted(documented_names - all_declared_names)

    total_fields = len(Settings.model_fields)
    return {
        "env_example_path": str(path),
        "env_example_found": path.exists(),
        "total_settings_fields": total_fields,
        "documented_field_count": documented_field_count,
        "undocumented_field_count": len(undocumented_fields),
        "undocumented_fields": undocumented_fields,
        "extra_in_env_example": extra_in_env_example,
        "coverage_ratio": round(documented_field_count / total_fields, 3) if total_fields else 1.0,
    }

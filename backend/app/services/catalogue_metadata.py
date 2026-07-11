from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.version import BUILD_STAGE

logger = logging.getLogger("uvicorn.error.hive.catalogue_metadata")


CATALOGUE_SCHEMA_VERSION = "2026-06-22.catalogue-metadata.v1"


def repo_root() -> Path:
    """Return the HIVE repository root from this service module."""

    return Path(__file__).resolve().parents[3]


def load_skill_catalogue_metadata() -> dict[str, object]:
    return _load_catalogue_file("skills/catalogue_metadata.json")


def load_task_catalogue_metadata() -> dict[str, object]:
    return _load_catalogue_file("tasks/task_metadata.json")


@lru_cache(maxsize=8)
def _load_catalogue_file(relative_path: str) -> dict[str, object]:
    path = repo_root() / relative_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.error(
            "Bundled catalogue file failed to load path=%s error_type=%s error=%s",
            relative_path,
            type(error).__name__,
            error,
        )
        return {
            "schema_version": CATALOGUE_SCHEMA_VERSION,
            "updated_at": None,
            "description": f"{relative_path} is unavailable or invalid.",
            "defaults": {},
            "items": [],
            "fallback_rules": [],
        }
    if not isinstance(payload, dict):
        return {"schema_version": CATALOGUE_SCHEMA_VERSION, "defaults": {}, "items": []}
    return payload


def catalogue_status() -> dict[str, object]:
    skills = load_skill_catalogue_metadata()
    tasks = load_task_catalogue_metadata()
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "skills": _catalogue_summary(skills, "skills/catalogue_metadata.json"),
        "tasks": _catalogue_summary(tasks, "tasks/task_metadata.json"),
    }


def enrich_skill_item(item: dict[str, Any]) -> dict[str, object]:
    """Add stable local catalogue metadata to a skill registry item.

    D1/R2 skills are the source of truth for skill availability. This enrichment
    layer supplies UI/operator metadata only, keeping imported records useful even
    when upstream descriptors omit a short description.
    """

    payload = dict(item)
    meta = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
    catalogue = load_skill_catalogue_metadata()
    match = _metadata_match(catalogue, _skill_candidate_ids(payload, meta))
    fallback = _fallback_for_skill(catalogue, payload, meta)
    defaults = _defaults(catalogue)
    merged = {**defaults, **fallback, **match}

    description = _clean_text(
        payload.get("description")
        or meta.get("description")
        or merged.get("description")
        or _generated_skill_description(payload, meta, merged)
    )
    title = _clean_text(payload.get("title") or meta.get("title") or merged.get("title") or meta.get("name") or meta.get("slug") or payload.get("source_id") or "Skill")
    category = _clean_text(meta.get("catalogue_category") or merged.get("category") or defaults.get("category") or "General operations")
    risk = _normalise_risk(meta.get("risk_level") or merged.get("risk") or defaults.get("risk"))
    requires_approval = _bool_or_default(
        merged.get("requires_approval"),
        default=risk in {"medium", "high"},
    )

    meta.update(
        {
            "description": description,
            "short_description": description,
            "catalogue_category": category,
            "risk_level": risk,
            "requires_approval": requires_approval,
            "when_to_use": _clean_text(merged.get("when_to_use") or defaults.get("when_to_use")),
            "metadata_source": "skills/catalogue_metadata.json",
            "metadata_schema_version": str(catalogue.get("schema_version") or CATALOGUE_SCHEMA_VERSION),
        }
    )
    payload.update(
        {
            "title": title,
            "description": description,
            "category": category,
            "risk_level": risk,
            "requires_approval": requires_approval,
            "metadata": meta,
        }
    )
    return payload


def enrich_skill_items(items: list[dict[str, Any]]) -> list[dict[str, object]]:
    return [enrich_skill_item(item) for item in items if isinstance(item, dict)]


def enrich_task_item(item: dict[str, Any], *, item_id: str | None = None) -> dict[str, object]:
    payload = dict(item)
    catalogue = load_task_catalogue_metadata()
    candidates = _task_candidate_ids(payload, item_id)
    defaults = _defaults(catalogue)
    match = _metadata_match(catalogue, candidates)
    merged = {**defaults, **match}
    risk = _normalise_risk(payload.get("risk") or payload.get("risk_level") or merged.get("risk"))
    description = _clean_text(
        payload.get("description")
        or payload.get("summary")
        or merged.get("description")
        or _generated_task_description(payload, merged)
    )
    title = _clean_text(payload.get("title") or payload.get("label") or payload.get("name") or merged.get("title") or item_id or "Task")
    payload.update(
        {
            "title": title,
            "description": description,
            "category": _clean_text(payload.get("category") or merged.get("category") or defaults.get("category") or "Operations"),
            "risk": risk,
            "requires_approval": _bool_or_default(
                payload.get("requires_approval") if "requires_approval" in payload else merged.get("requires_approval"),
                default=risk in {"medium", "high"},
            ),
            "when_to_use": _clean_text(payload.get("when_to_use") or merged.get("when_to_use") or defaults.get("when_to_use")),
            "metadata_source": "tasks/task_metadata.json",
            "metadata_schema_version": str(catalogue.get("schema_version") or CATALOGUE_SCHEMA_VERSION),
        }
    )
    return payload


def task_metadata_item(item_id: str) -> dict[str, object]:
    return enrich_task_item({"id": item_id}, item_id=item_id)


def _catalogue_summary(catalogue: dict[str, object], path: str) -> dict[str, object]:
    items = catalogue.get("items") if isinstance(catalogue.get("items"), list) else []
    missing = []
    for item in items:
        if not isinstance(item, dict):
            continue
        missing_fields = [
            field
            for field in (catalogue.get("required_fields") or [])
            if not _clean_text(item.get(str(field))) and str(field) != "requires_approval"
        ]
        if missing_fields:
            missing.append({"id": item.get("id"), "missing_fields": missing_fields})
    return {
        "path": path,
        "schema_version": catalogue.get("schema_version"),
        "updated_at": catalogue.get("updated_at"),
        "item_count": len(items),
        "fallback_rule_count": len(catalogue.get("fallback_rules") or []),
        "missing_required_count": len(missing),
        "missing_required": missing[:20],
    }


def _metadata_match(catalogue: dict[str, object], candidates: list[str]) -> dict[str, object]:
    normalised = {_normalise_id(value) for value in candidates if _normalise_id(value)}
    for item in catalogue.get("items") or []:
        if not isinstance(item, dict):
            continue
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        item_candidates = {_normalise_id(str(item.get("id") or ""))}
        item_candidates.update(_normalise_id(str(alias)) for alias in aliases)
        if normalised.intersection(item_candidates):
            return dict(item)
    return {}


def _fallback_for_skill(
    catalogue: dict[str, object], item: dict[str, Any], meta: dict[str, Any]
) -> dict[str, object]:
    haystack = _normalise_words(
        " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("source_id") or ""),
                str(meta.get("slug") or ""),
                str(meta.get("name") or ""),
                str(meta.get("hive_lane") or ""),
                str(meta.get("catalogue_category") or ""),
                " ".join(str(tag) for tag in meta.get("tags") or []),
                " ".join(str(repo) for repo in meta.get("repos") or []),
                str(meta.get("indexable_text") or "")[:600],
            ]
        )
    )
    for rule in catalogue.get("fallback_rules") or []:
        if not isinstance(rule, dict):
            continue
        terms = [_normalise_id(str(term)) for term in (rule.get("match_any") or [])]
        if any(term and term in haystack for term in terms):
            return dict(rule)
    return {}


def _skill_candidate_ids(item: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    return [
        str(item.get("id") or ""),
        str(item.get("source_id") or ""),
        str(item.get("title") or ""),
        str(meta.get("skill_id") or ""),
        str(meta.get("reference_prefix") or ""),
        str(meta.get("slug") or ""),
        str(meta.get("name") or ""),
    ]


def _task_candidate_ids(item: dict[str, Any], item_id: str | None) -> list[str]:
    return [
        str(item_id or ""),
        str(item.get("id") or ""),
        str(item.get("name") or ""),
        str(item.get("label") or ""),
        str(item.get("title") or ""),
        str(item.get("template") or ""),
    ]


def _generated_skill_description(
    item: dict[str, Any], meta: dict[str, Any], merged: dict[str, object]
) -> str:
    category = _clean_text(meta.get("catalogue_category") or merged.get("category") or "operations")
    repos = [str(repo) for repo in (meta.get("repos") or []) if str(repo).strip()]
    repo_note = f" for {', '.join(repos[:3])}" if repos else ""
    return f"Supports {category.lower()}{repo_note} using governed skill metadata and review-gated planning."


def _generated_task_description(item: dict[str, Any], merged: dict[str, object]) -> str:
    title = _clean_text(item.get("label") or item.get("title") or item.get("name") or item.get("id") or "This task")
    category = _clean_text(merged.get("category") or item.get("category") or "operations")
    return f"Runs the {title.lower()} stage for {category.lower()} with bounded, review-aware behaviour."


def _defaults(catalogue: dict[str, object]) -> dict[str, object]:
    defaults = catalogue.get("defaults")
    return dict(defaults) if isinstance(defaults, dict) else {}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _normalise_id(value: str) -> str:
    clean = str(value or "").strip().lower()
    if clean.startswith("skill:"):
        clean = clean.split(":", 1)[1]
    for ch in [" ", "_", "/", ".", ":"]:
        clean = clean.replace(ch, "-")
    return "-".join(part for part in clean.split("-") if part)


def _normalise_words(value: str) -> str:
    clean = _normalise_id(value)
    return clean.replace("-", " ") + " " + clean


def _normalise_risk(value: Any) -> str:
    risk = str(value or "medium").strip().lower()
    return risk if risk in {"low", "medium", "high"} else "medium"


def _bool_or_default(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        clean = value.strip().lower()
        if clean in {"true", "1", "yes", "y", "on"}:
            return True
        if clean in {"false", "0", "no", "n", "off"}:
            return False
    return default

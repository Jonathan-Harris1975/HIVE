from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any

import httpx

from app.core.config import Settings
from app.storage.d1 import D1MetadataStore

SKILL_LANE = "hive_skills"
SEARCH_DOCUMENTS_KEY = "index/search-documents.json"
SKILLS_INDEX_KEY = "index/skills-index.json"
SHARED_MANIFEST_KEY = "manifests/shared-skill-pool-manifest.json"


def skills_registry_status(settings: Settings) -> dict[str, object]:
    """Return lightweight status for the R2-backed shared skill pool."""

    base = settings.public_url_for_r2_lane(SKILL_LANE, "")
    d1 = D1MetadataStore(settings)
    count_payload = _d1_skill_counts(d1)
    return {
        "ok": True,
        "build_stage_hint": "v1.8-skill-registry-import",
        "lane": SKILL_LANE,
        "configured": bool(base),
        "public_base_url": base,
        "search_documents_url": settings.public_url_for_r2_lane(SKILL_LANE, SEARCH_DOCUMENTS_KEY),
        "skills_index_url": settings.public_url_for_r2_lane(SKILL_LANE, SKILLS_INDEX_KEY),
        "shared_manifest_url": settings.public_url_for_r2_lane(SKILL_LANE, SHARED_MANIFEST_KEY),
        "d1": {
            "enabled": d1.enabled,
            "indexed_skill_count": count_payload.get("count") if count_payload.get("ok") else None,
            "count_probe": count_payload,
        },
        "source_of_truth": "R2 hive-skills descriptors + HIVE_skills_availability_register_v2_repo_mapped.xlsx",
        "note": "v1.8 imports R2 skill search documents into D1 for fast catalogue/list/search operations.",
    }


def import_skills_manifest(
    *,
    settings: Settings,
    dry_run: bool = True,
    limit: int | None = None,
    search_documents_url: str | None = None,
) -> dict[str, object]:
    """Import the shared skill pool search documents into D1.

    The importer is deliberately bounded and synchronous for Koyeb Free. It uses
    the compact `index/search-documents.json` generated from the skills register
    and descriptor JSON rather than fetching every individual descriptor.
    """

    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled", **_skill_manifest_hints(settings)}

    url = search_documents_url or settings.public_url_for_r2_lane(SKILL_LANE, SEARCH_DOCUMENTS_KEY)
    if not url:
        return {"ok": False, "error_code": "skills_manifest_url_missing", **_skill_manifest_hints(settings)}

    docs_payload = _fetch_json(url, timeout=settings.skill_registry_import_timeout_seconds)
    if not docs_payload.get("ok"):
        return {"ok": False, "stage": "fetch_search_documents", "url": url, **docs_payload, **_skill_manifest_hints(settings)}

    raw = docs_payload.get("json")
    documents = _extract_search_documents(raw)
    safe_limit = _safe_import_limit(settings, limit)
    selected = documents[:safe_limit]

    prepared = [_skill_document_to_metadata(settings, doc) for doc in selected]
    stats = _skill_stats_from_items([item["metadata"] for item in prepared])
    if dry_run:
        return {
            "ok": True,
            "enabled": True,
            "dry_run": True,
            "source_url": url,
            "available_documents": len(documents),
            "prepared_count": len(prepared),
            "import_limit": safe_limit,
            "stats": stats,
            "sample": [item["metadata"] for item in prepared[:5]],
            **_skill_manifest_hints(settings),
        }

    results: list[dict[str, object]] = []
    imported = 0
    failures = 0
    started = time.time()
    for item in prepared:
        result = d1.upsert_metadata(
            item_id=item["id"],
            lane=SKILL_LANE,
            source_type="skill_descriptor",
            source_id=item["source_id"],
            title=item["title"],
            url=item["url"],
            metadata=item["metadata"],
        )
        if result.get("ok"):
            imported += 1
        else:
            failures += 1
            if len(results) < 5:
                results.append({"skill_id": item["source_id"], "result": result})

    return {
        "ok": failures == 0,
        "enabled": True,
        "dry_run": False,
        "source_url": url,
        "available_documents": len(documents),
        "imported_count": imported,
        "failure_count": failures,
        "import_limit": safe_limit,
        "elapsed_seconds": round(time.time() - started, 3),
        "stats": stats,
        "sample_failures": results,
        **_skill_manifest_hints(settings),
    }


def list_skills_catalogue(
    *,
    settings: Settings,
    limit: int = 50,
    repo: str | None = None,
    hive_lane: str | None = None,
    priority_tier: str | None = None,
    risk_level: str | None = None,
) -> dict[str, object]:
    payload = _skill_records(settings=settings, query=None, limit=limit)
    if not payload.get("ok"):
        return payload
    items = _filter_skill_items(
        payload.get("items", []),
        repo=repo,
        hive_lane=hive_lane,
        priority_tier=priority_tier,
        risk_level=risk_level,
    )[: max(1, min(int(limit or 50), 500))]
    return {
        "ok": True,
        "lane": SKILL_LANE,
        "count": len(items),
        "items": items,
        "filters": _filters(repo=repo, hive_lane=hive_lane, priority_tier=priority_tier, risk_level=risk_level),
        "grouped": _group_skill_items(items),
        "source": "d1:hive_ecosystem_metadata",
        **_skill_manifest_hints(settings),
    }


def search_skills_catalogue(
    *,
    settings: Settings,
    query: str,
    limit: int = 25,
    repo: str | None = None,
    hive_lane: str | None = None,
    priority_tier: str | None = None,
    risk_level: str | None = None,
) -> dict[str, object]:
    payload = _skill_records(settings=settings, query=query, limit=max(limit, 100))
    if not payload.get("ok"):
        return payload
    q = " ".join((query or "").strip().split())[:300]
    items = _filter_skill_items(
        payload.get("items", []),
        repo=repo,
        hive_lane=hive_lane,
        priority_tier=priority_tier,
        risk_level=risk_level,
    )
    scored = [_score_skill_item(item, q) for item in items]
    scored.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    trimmed = scored[: max(1, min(int(limit or 25), 200))]
    return {
        "ok": True,
        "lane": SKILL_LANE,
        "query": q,
        "count": len(trimmed),
        "items": trimmed,
        "filters": _filters(repo=repo, hive_lane=hive_lane, priority_tier=priority_tier, risk_level=risk_level),
        "source": "d1:hive_ecosystem_metadata",
        **_skill_manifest_hints(settings),
    }


def skill_categories(settings: Settings, limit: int = 500) -> dict[str, object]:
    payload = _skill_records(settings=settings, query=None, limit=limit)
    if not payload.get("ok"):
        return payload
    items = payload.get("items", [])
    counters: dict[str, Counter[str]] = {
        "priority_tiers": Counter(),
        "hive_lanes": Counter(),
        "risk_levels": Counter(),
        "repos": Counter(),
        "tags": Counter(),
    }
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for field, counter_name in [("priority_tier", "priority_tiers"), ("hive_lane", "hive_lanes"), ("risk_level", "risk_levels")]:
            value = str(meta.get(field) or "").strip()
            if value:
                counters[counter_name][value] += 1
        for repo in meta.get("repos") or []:
            counters["repos"][str(repo)] += 1
        for tag in meta.get("tags") or []:
            counters["tags"][str(tag)] += 1
    return {
        "ok": True,
        "lane": SKILL_LANE,
        "indexed_skill_count": len(items),
        "categories": {name: dict(counter.most_common(50)) for name, counter in counters.items()},
        **_skill_manifest_hints(settings),
    }


def _skill_records(*, settings: Settings, query: str | None, limit: int) -> dict[str, object]:
    from app.services.ecosystem_index import recent_ecosystem_metadata, search_ecosystem_metadata

    safe_limit = max(1, min(int(limit or 50), 500))
    if query:
        payload = search_ecosystem_metadata(settings=settings, query=query, lane=SKILL_LANE, limit=safe_limit)
    else:
        payload = recent_ecosystem_metadata(settings=settings, lane=SKILL_LANE, limit=safe_limit)
    if not payload.get("ok"):
        payload.update(_skill_manifest_hints(settings))
        payload["note"] = "No D1 skill records are available yet. Run POST /v1/skills/import-manifest first."
        return payload
    return payload


def _extract_search_documents(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        docs = payload.get("documents")
        if isinstance(docs, list):
            return [doc for doc in docs if isinstance(doc, dict)]
    if isinstance(payload, list):
        return [doc for doc in payload if isinstance(doc, dict)]
    return []


def _skill_document_to_metadata(settings: Settings, doc: dict[str, Any]) -> dict[str, Any]:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    reference = str(metadata.get("reference_prefix") or doc.get("reference_prefix") or doc.get("skill_id") or "").strip()
    skill_id = str(metadata.get("skill_id") or reference).strip()
    name = str(doc.get("name") or metadata.get("slug") or skill_id).strip()
    object_key = str(doc.get("object_key") or metadata.get("object_key") or f"skills/{reference}_{name}.json").strip()
    tags = [str(tag) for tag in (doc.get("tags") or metadata.get("tags") or []) if str(tag).strip()]
    repos = [str(repo) for repo in (metadata.get("repos") or []) if str(repo).strip()]
    search_text = str(doc.get("text") or metadata.get("search_text") or "")
    enriched_metadata = {
        "skill_id": skill_id,
        "reference_prefix": reference,
        "slug": metadata.get("slug") or name,
        "name": name,
        "object_key": object_key,
        "descriptor_url": settings.public_url_for_r2_lane(SKILL_LANE, object_key),
        "search_document_id": doc.get("document_id") or f"skill:{reference}",
        "priority_tier": metadata.get("priority_tier"),
        "hive_lane": metadata.get("hive_lane"),
        "risk_level": metadata.get("risk_level"),
        "repos": repos,
        "tags": tags,
        "catalogue_category": _catalogue_category(metadata, tags),
        "indexable_text": search_text,
        "source_register": "HIVE_skills_availability_register_v2_repo_mapped.xlsx",
        "source_manifest_key": SEARCH_DOCUMENTS_KEY,
    }
    return {
        "id": f"skill:{skill_id or reference or name}",
        "source_id": skill_id or reference or name,
        "title": name,
        "url": enriched_metadata["descriptor_url"],
        "metadata": enriched_metadata,
    }


def _catalogue_category(metadata: dict[str, Any], tags: list[str]) -> str:
    lane = str(metadata.get("hive_lane") or "").lower()
    joined = " ".join(tags).lower()
    if "seo" in joined or "rss" in joined or "content" in joined:
        return "content-operations"
    if "cloudflare" in joined or "deploy" in joined or "devops" in joined or "infra" in joined:
        return "infrastructure-operations"
    if "security" in joined or "risk" in joined or "audit" in joined:
        return "risk-and-audit"
    if "hive core" in lane or "skill" in joined:
        return "skill-governance"
    if "repo" in lane or "github" in joined or "code" in joined:
        return "repo-engineering"
    return (metadata.get("hive_lane") or "general").strip().lower().replace(" ", "-")


def _score_skill_item(item: dict[str, Any], query: str) -> dict[str, object]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    haystack = " ".join(str(part or "") for part in [item.get("title"), item.get("source_type"), item.get("source_id"), meta, item.get("url")]).lower()
    terms = [term for term in query.lower().split() if len(term) > 1]
    matched = [term for term in terms if term in haystack]
    payload = dict(item)
    payload["score"] = round(len(matched) / max(1, len(terms)), 3)
    payload["matched_terms"] = matched
    return payload


def _filter_skill_items(
    items: list[dict[str, Any]],
    *,
    repo: str | None,
    hive_lane: str | None,
    priority_tier: str | None,
    risk_level: str | None,
) -> list[dict[str, Any]]:
    clean_repo = (repo or "").strip().lower()
    clean_lane = (hive_lane or "").strip().lower()
    clean_priority = (priority_tier or "").strip().lower()
    clean_risk = (risk_level or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if clean_repo and clean_repo not in [str(repo).lower() for repo in meta.get("repos") or []]:
            continue
        if clean_lane and clean_lane not in str(meta.get("hive_lane") or "").lower():
            continue
        if clean_priority and clean_priority not in str(meta.get("priority_tier") or "").lower():
            continue
        if clean_risk and clean_risk != str(meta.get("risk_level") or "").lower():
            continue
        filtered.append(item)
    return filtered


def _group_skill_items(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counters: dict[str, Counter[str]] = {"hive_lane": Counter(), "priority_tier": Counter(), "risk_level": Counter()}
    for item in items:
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for field, counter in counters.items():
            value = str(meta.get(field) or "").strip()
            if value:
                counter[value] += 1
    return {name: dict(counter) for name, counter in counters.items()}


def _skill_stats_from_items(items: list[dict[str, Any]]) -> dict[str, object]:
    return {
        "count": len(items),
        "by_priority_tier": dict(Counter(str(item.get("priority_tier") or "unknown") for item in items)),
        "by_hive_lane": dict(Counter(str(item.get("hive_lane") or "unknown") for item in items)),
        "by_risk_level": dict(Counter(str(item.get("risk_level") or "unknown") for item in items)),
        "by_repo": dict(Counter(repo for item in items for repo in (item.get("repos") or []))),
        "by_catalogue_category": dict(Counter(str(item.get("catalogue_category") or "unknown") for item in items)),
    }


def _d1_skill_counts(d1: D1MetadataStore) -> dict[str, object]:
    if not d1.enabled:
        return {"ok": False, "enabled": False}
    result = d1.query("SELECT COUNT(*) AS count FROM hive_ecosystem_metadata WHERE lane = ? AND source_type = ?", [SKILL_LANE, "skill_descriptor"])
    if not result.get("ok"):
        return result
    from app.storage.d1 import _extract_d1_rows  # small internal helper; safe bounded usage

    rows = _extract_d1_rows(result.get("result"))
    return {"ok": True, "enabled": True, "count": rows[0].get("count") if rows else 0}


def _fetch_json(url: str, timeout: int) -> dict[str, object]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return {"ok": False, "status_code": response.status_code, "message": response.text[:500]}
        return {"ok": True, "status_code": response.status_code, "json": response.json()}
    except Exception as exc:  # pragma: no cover - network only
        return {"ok": False, "message": str(exc), "error_type": type(exc).__name__}


def _safe_import_limit(settings: Settings, limit: int | None) -> int:
    configured = max(1, min(int(settings.skill_registry_import_max_items or 250), 1000))
    if limit is None:
        return configured
    return max(1, min(int(limit), configured))


def _skill_manifest_hints(settings: Settings) -> dict[str, object]:
    return {
        "lane_public_base_url": settings.public_url_for_r2_lane(SKILL_LANE, ""),
        "manifest_hint": settings.public_url_for_r2_lane(SKILL_LANE, "index/skills-manifest.json"),
        "search_documents_hint": settings.public_url_for_r2_lane(SKILL_LANE, SEARCH_DOCUMENTS_KEY),
        "skills_index_hint": settings.public_url_for_r2_lane(SKILL_LANE, SKILLS_INDEX_KEY),
    }


def _filters(**kwargs: str | None) -> dict[str, str]:
    return {key: value for key, value in kwargs.items() if value}

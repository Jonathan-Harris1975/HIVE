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
        "build_stage_hint": "v1.12-shared-ecosystem-execution-layer",
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
        "note": "v1.14 uses the imported R2 skill registry with intelligent search, recommendations, review-gated routing, plan-only shared execution, and a D1 execution review queue.",
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




def get_skill_detail(*, settings: Settings, skill_id: str) -> dict[str, object]:
    """Return one indexed skill by id, reference prefix, slug or title."""

    needle = _normalise_lookup(skill_id)
    payload = _skill_records(settings=settings, query=None, limit=500)
    if not payload.get("ok"):
        return payload
    for item in payload.get("items", []):
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        candidates = [
            item.get("id"),
            item.get("source_id"),
            item.get("title"),
            meta.get("skill_id"),
            meta.get("reference_prefix"),
            meta.get("slug"),
            meta.get("name"),
            meta.get("search_document_id"),
        ]
        if needle in {_normalise_lookup(str(value)) for value in candidates if value}:
            enriched = _score_skill_item(item, str(skill_id or ""))
            enriched["execution_policy"] = _execution_policy_for_skill(enriched)
            return {"ok": True, "lane": SKILL_LANE, "skill": enriched, **_skill_manifest_hints(settings)}
    return {"ok": False, "lane": SKILL_LANE, "error_code": "skill_not_found", "skill_id": skill_id, **_skill_manifest_hints(settings)}


def skills_by_repo(*, settings: Settings, repo: str, limit: int = 100) -> dict[str, object]:
    return list_skills_catalogue(settings=settings, limit=limit, repo=repo)


def skills_by_risk(*, settings: Settings, risk_level: str, limit: int = 100) -> dict[str, object]:
    return list_skills_catalogue(settings=settings, limit=limit, risk_level=risk_level)


def skills_by_lane(*, settings: Settings, hive_lane: str, limit: int = 100) -> dict[str, object]:
    return list_skills_catalogue(settings=settings, limit=limit, hive_lane=hive_lane)


def recommend_skills(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    hive_lane: str | None = None,
    risk_ceiling: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    """Recommend skills for a task using weighted D1 catalogue metadata.

    This is deliberately a recommendation/router layer, not an auto-install or
    auto-execution layer. HIVE should explain the safest candidate set first.
    """

    q = " ".join((task or "").strip().split())[:500]
    if not q:
        return {"ok": False, "error_code": "missing_task", "message": "task is required."}
    payload = _skill_records(settings=settings, query=None, limit=500)
    if not payload.get("ok"):
        return payload
    items = _filter_skill_items(
        payload.get("items", []),
        repo=repo,
        hive_lane=hive_lane,
        priority_tier=None,
        risk_level=None,
    )
    if risk_ceiling:
        items = [item for item in items if _risk_allowed(item, risk_ceiling)]
    scored = [_score_skill_item(item, q, recommendation_mode=True) for item in items]
    scored.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    selected = scored[: max(1, min(int(limit or 10), 50))]
    return {
        "ok": True,
        "lane": SKILL_LANE,
        "task": q,
        "count": len(selected),
        "recommendations": [_recommendation_summary(item) for item in selected],
        "items": selected,
        "filters": _filters(repo=repo, hive_lane=hive_lane, risk_ceiling=risk_ceiling),
        "safety_note": "Recommendations are registry-only. HIVE does not install or execute repo skills without a later explicit execution gate.",
        **_skill_manifest_hints(settings),
    }


def route_skill_request(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    hive_lane: str | None = None,
    limit: int = 5,
) -> dict[str, object]:
    recs = recommend_skills(settings=settings, task=task, repo=repo, hive_lane=hive_lane, limit=limit)
    if not recs.get("ok"):
        return recs
    items = recs.get("items", [])
    primary = items[0] if items else None
    route = _route_plan(task=task, primary=primary, candidates=items)
    return {
        "ok": True,
        "task": recs.get("task"),
        "repo": repo,
        "hive_lane": hive_lane,
        "primary_skill": _recommendation_summary(primary) if isinstance(primary, dict) else None,
        "candidate_count": len(items),
        "candidate_skills": [_recommendation_summary(item) for item in items],
        "route_plan": route,
        "execution_policy": "review_gated",
        "free_tier_note": "Routing is metadata-only and safe for Koyeb Free; no background execution is started.",
        **_skill_manifest_hints(settings),
    }


def shared_execution_plan(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    limit: int = 5,
) -> dict[str, object]:
    """Return a shared ecosystem execution plan without running tools.

    v1.14 stores reviewable execution plans and decisions. The next safe step is to add
    explicit per-skill execution adapters behind allowlists and dry-run gates.
    """

    routed = route_skill_request(settings=settings, task=task, repo=repo, hive_lane=None, limit=limit)
    if not routed.get("ok"):
        return routed
    steps = [
        {"step": 1, "name": "classify_task", "description": "Confirm repo/lane/workflow intent and risk level."},
        {"step": 2, "name": "select_skills", "description": "Use HIVE skill registry recommendations as the candidate set."},
        {"step": 3, "name": "load_sources", "description": "Collect relevant R2/D1/PostgreSQL evidence before proposing changes."},
        {"step": 4, "name": "dry_run", "description": "Produce a dry-run output or patch plan; no live repo/system mutation."},
        {"step": 5, "name": "approval_gate", "description": "Require explicit approval before any future execution adapter runs."},
    ]
    return {
        "ok": True,
        "build_stage_hint": "v1.12-shared-ecosystem-execution-layer",
        "task": task,
        "repo": repo,
        "workflow_preset": workflow_preset,
        "execution_mode": "plan_only",
        "can_execute_now": False,
        "requires_approval": True,
        "routed_skill_plan": routed,
        "shared_steps": steps,
        "guardrails": {
            "no_auto_install": True,
            "no_background_jobs_on_koyeb_free": True,
            "dry_run_first": True,
            "risk_gates_required": ["medium", "high"],
        },
        "next_adapter_layer": "v1.15 can add explicit execution adapters for selected low-risk registry tasks.",
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


def _score_skill_item(item: dict[str, Any], query: str, recommendation_mode: bool = False) -> dict[str, object]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    terms = _query_terms(query)
    field_values = {
        "title": str(item.get("title") or ""),
        "slug": str(meta.get("slug") or ""),
        "tags": " ".join(str(tag) for tag in (meta.get("tags") or [])),
        "hive_lane": str(meta.get("hive_lane") or ""),
        "catalogue_category": str(meta.get("catalogue_category") or ""),
        "repos": " ".join(str(repo) for repo in (meta.get("repos") or [])),
        "priority_tier": str(meta.get("priority_tier") or ""),
        "risk_level": str(meta.get("risk_level") or ""),
        "indexable_text": str(meta.get("indexable_text") or ""),
        "url": str(item.get("url") or ""),
    }
    weights = {
        "title": 12,
        "slug": 10,
        "tags": 8,
        "hive_lane": 7,
        "catalogue_category": 5,
        "repos": 3,
        "priority_tier": 2,
        "risk_level": 1,
        "indexable_text": 2,
        "url": 1,
    }
    matched_terms: list[str] = []
    matched_fields: dict[str, list[str]] = {}
    raw_score = 0.0
    for field, text in field_values.items():
        normalised = _normalise_text(text)
        field_matches = [term for term in terms if _term_matches(term, normalised)]
        if not field_matches:
            continue
        matched_fields[field] = field_matches
        matched_terms.extend(field_matches)
        raw_score += weights.get(field, 1) * len(set(field_matches))
    if recommendation_mode:
        raw_score += _priority_boost(meta)
        raw_score += _risk_boost(meta)
    max_possible = max(1.0, len(terms) * sum(weights.values()) * 0.35)
    score = min(100.0, round((raw_score / max_possible) * 100, 2)) if terms else 0.0
    payload = dict(item)
    payload["score"] = score
    payload["matched_terms"] = sorted(set(matched_terms))
    payload["matched_fields"] = matched_fields
    payload["score_breakdown"] = {
        "raw_score": round(raw_score, 2),
        "term_count": len(terms),
        "recommendation_mode": recommendation_mode,
        "priority_boost": _priority_boost(meta) if recommendation_mode else 0,
        "risk_boost": _risk_boost(meta) if recommendation_mode else 0,
    }
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




def _normalise_lookup(value: str) -> str:
    return _normalise_text(value).replace("skill:", "").strip()


def _normalise_text(value: str) -> str:
    return " ".join(str(value or "").lower().replace("_", " ").replace("-", " ").replace("/", " ").split())


def _query_terms(query: str) -> list[str]:
    raw = _normalise_text(query)
    stop = {"the", "and", "for", "with", "from", "this", "that", "into", "over", "using"}
    return [term for term in raw.split() if len(term) > 1 and term not in stop]


def _term_matches(term: str, normalised_text: str) -> bool:
    if term in normalised_text:
        return True
    # Small synonym bridge for current ecosystem wording.
    synonyms = {
        "rss": ["feed", "feeds"],
        "rewrite": ["rewriting", "rewrites", "content", "copy"],
        "podseo": ["podcast seo", "podcast"],
        "podcast": ["podseo"],
        "seo": ["aeo", "geo", "search"],
    }
    return any(alias in normalised_text for alias in synonyms.get(term, []))


def _priority_boost(meta: dict[str, Any]) -> float:
    value = str(meta.get("priority_tier") or "").lower()
    if "p0" in value:
        return 8.0
    if "p1" in value:
        return 5.0
    if "p2" in value:
        return 2.0
    return 0.0


def _risk_boost(meta: dict[str, Any]) -> float:
    value = str(meta.get("risk_level") or "").lower()
    if value == "low":
        return 4.0
    if value == "medium":
        return 1.5
    if value == "high":
        return -2.0
    return 0.0


def _risk_allowed(item: dict[str, Any], risk_ceiling: str) -> bool:
    order = {"low": 1, "medium": 2, "high": 3}
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    current = order.get(str(meta.get("risk_level") or "").lower(), 3)
    ceiling = order.get(str(risk_ceiling or "").lower(), 3)
    return current <= ceiling


def _recommendation_summary(item: dict[str, Any] | None) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "skill_id": meta.get("skill_id") or item.get("source_id"),
        "title": item.get("title"),
        "score": item.get("score"),
        "priority_tier": meta.get("priority_tier"),
        "risk_level": meta.get("risk_level"),
        "hive_lane": meta.get("hive_lane"),
        "repos": meta.get("repos") or [],
        "matched_terms": item.get("matched_terms") or [],
        "matched_fields": item.get("matched_fields") or {},
        "descriptor_url": meta.get("descriptor_url") or item.get("url"),
        "execution_policy": _execution_policy_for_skill(item),
    }


def _execution_policy_for_skill(item: dict[str, Any]) -> dict[str, object]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    risk = str(meta.get("risk_level") or "medium").lower()
    return {
        "risk_level": risk,
        "auto_execute_allowed": False,
        "review_required": risk in {"medium", "high"},
        "install_allowed": False,
        "notes": "Registry routing only. Review descriptor and repo impact before any execution/install step.",
    }


def _route_plan(task: str, primary: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> list[dict[str, object]]:
    primary_summary = _recommendation_summary(primary) if isinstance(primary, dict) else None
    return [
        {"step": 1, "name": "understand_task", "description": f"Classify request: {task[:160]}"},
        {"step": 2, "name": "select_primary_skill", "description": "Pick highest-scoring registry candidate.", "primary_skill": primary_summary},
        {"step": 3, "name": "gather_evidence", "description": "Load relevant repo/R2/D1 evidence before suggesting changes."},
        {"step": 4, "name": "dry_run_response", "description": "Return a reviewable plan/output only; do not mutate systems."},
        {"step": 5, "name": "approval_gate", "description": "Require explicit approval before any future execution adapter runs."},
    ]

def _filters(**kwargs: str | None) -> dict[str, str]:
    return {key: value for key, value in kwargs.items() if value}

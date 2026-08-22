from __future__ import annotations

from collections import Counter
from typing import Any

SCORE_WEIGHTS = {
    "exact_title": 50,
    "title": 24,
    "slug": 20,
    "tags": 16,
    "hive_lane": 14,
    "catalogue_category": 10,
    "repos": 8,
    "indexable_text": 4,
}

SKILL_SYNONYMS = {
    "rss": ["rss", "feed", "feeds", "syndication"],
    "rewrite": ["rewrite", "rewriter", "rewriting", "rewrites", "copy", "content"],
    "seo": ["seo", "aeo", "geo", "search", "metadata"],
    "podcast": ["podcast", "episode", "audio", "transcript", "podseo"],
    "audit": ["audit", "audits", "rams", "qa", "review", "verification"],
    "social": ["social", "facebook", "instagram", "tiktok", "youtube", "content"],
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
    terms = _query_terms(query)
    fields = {
        "title": str(item.get("title") or ""),
        "slug": str(meta.get("slug") or meta.get("name") or ""),
        "tags": " ".join(str(tag) for tag in (meta.get("tags") or [])),
        "hive_lane": str(meta.get("hive_lane") or ""),
        "catalogue_category": str(meta.get("catalogue_category") or ""),
        "repos": " ".join(str(repo) for repo in (meta.get("repos") or [])),
        "indexable_text": str(meta.get("indexable_text") or ""),
    }
    score = 0
    matched_terms: set[str] = set()
    matched_fields: dict[str, list[str]] = {}
    title_norm = _normalise_text(fields["title"])
    query_norm = _normalise_text(query)
    if title_norm and title_norm == query_norm:
        score += SCORE_WEIGHTS["exact_title"]
        matched_fields.setdefault("exact_title", []).append(fields["title"])

    for field, value in fields.items():
        normalised = _normalise_text(value)
        if not normalised:
            continue
        field_matches: list[str] = []
        for term in terms:
            variants = _term_variants(term)
            if any(variant and variant in normalised for variant in variants):
                score += SCORE_WEIGHTS.get(field, 1)
                matched_terms.add(term)
                field_matches.append(term)
        if field_matches:
            matched_fields[field] = sorted(set(field_matches))

    # Give P0/P1 and low-risk skills a small deterministic nudge after textual match.
    if score > 0:
        priority = str(meta.get("priority_tier") or "").lower()
        risk = str(meta.get("risk_level") or "").lower()
        if "p0" in priority:
            score += 6
        elif "p1" in priority:
            score += 3
        if risk == "low":
            score += 2

    payload = dict(item)
    payload["score"] = score
    payload["matched_terms"] = sorted(matched_terms)
    payload["matched_fields"] = matched_fields
    payload["score_explanation"] = _score_explanation(meta, matched_fields, score)
    return payload


def _query_terms(query: str) -> list[str]:
    cleaned = _normalise_text(query)
    terms = [term for term in cleaned.split() if len(term) > 1]
    # Keep a bounded term list so mobile/test calls cannot create silly work.
    return terms[:20]


def _term_variants(term: str) -> list[str]:
    variants = {term}
    variants.update(SKILL_SYNONYMS.get(term, []))
    if term.endswith("s") and len(term) > 3:
        variants.add(term[:-1])
    else:
        variants.add(term + "s")
    if term.endswith("ing") and len(term) > 5:
        variants.add(term[:-3])
    return sorted(variants, key=len, reverse=True)


def _normalise_text(value: str) -> str:
    value = str(value or "").lower()
    for ch in ["-", "_", "/", "|", ".", ",", ":", ";", "(", ")", "[", "]", "{", "}"]:
        value = value.replace(ch, " ")
    return " ".join(value.split())


def _normalise_skill_id(value: str) -> str:
    clean = str(value or "").strip().lower()
    if clean.startswith("skill:"):
        clean = clean.split(":", 1)[1]
    return clean


def _priority_sort_value(value: str) -> int:
    lower = value.lower()
    if "p0" in lower:
        return 3
    if "p1" in lower:
        return 2
    if "p2" in lower:
        return 1
    return 0


def _score_explanation(
    meta: dict[str, Any], matched_fields: dict[str, list[str]], score: int
) -> str:
    if score <= 0:
        return "No weighted field match."
    bits = []
    for field, terms in matched_fields.items():
        bits.append(f"{field}: {', '.join(terms[:6])}")
    priority = meta.get("priority_tier") or "unknown priority"
    risk = meta.get("risk_level") or "unknown risk"
    return f"Matched {len(matched_fields)} field group(s); {priority}; {risk}. " + "; ".join(
        bits[:6]
    )


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
    counters: dict[str, Counter[str]] = {
        "hive_lane": Counter(),
        "priority_tier": Counter(),
        "risk_level": Counter(),
    }
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
        "by_priority_tier": dict(
            Counter(str(item.get("priority_tier") or "unknown") for item in items)
        ),
        "by_hive_lane": dict(Counter(str(item.get("hive_lane") or "unknown") for item in items)),
        "by_risk_level": dict(Counter(str(item.get("risk_level") or "unknown") for item in items)),
        "by_repo": dict(Counter(repo for item in items for repo in (item.get("repos") or []))),
        "by_catalogue_category": dict(
            Counter(str(item.get("catalogue_category") or "unknown") for item in items)
        ),
    }



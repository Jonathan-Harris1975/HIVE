from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.services.repository_council import run_and_record_council
from app.services.repository_learning import update_project_dna
from app.services.repository_memory import append_history_entry
from app.services.repository_qa import run_repository_qa
from app.storage.d1 import D1MetadataStore

# Repository Intelligence is the canonical combined repository review. It runs
# Repository QA once, feeds that exact evidence into Repository Council, updates
# Project DNA and persists one consolidated improvement report. The raw QA and
# Council reports remain available in their existing history lanes, so no
# evidence is lost by presenting one operator workflow in HIVE-UI.

_SECURITY_CRITICAL_CHECKS = {"security_scanning"}
_HIGH_IMPACT_CHECKS = {"build_verification", "import_validation", "patch_verification"}

_QA_CONFIDENCE: dict[str, str] = {
    "build_verification": "measured",
    "lint": "heuristic",
    "type_checking": "heuristic",
    "dependency_validation": "measured",
    "import_validation": "measured",
    "dead_code_detection": "heuristic",
    "security_scanning": "heuristic",
    "regression_testing": "heuristic",
    "patch_verification": "measured",
    "architecture_validation": "heuristic",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _humanise(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _qa_severity(name: str) -> str:
    if name in _SECURITY_CRITICAL_CHECKS:
        return "critical"
    if name in _HIGH_IMPACT_CHECKS:
        return "high"
    return "medium"


def _council_severity(score: float) -> str:
    if score < 0.30:
        return "high"
    if score < 0.60:
        return "medium"
    return "low"


def _priority_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(severity, 4)


def _build_findings(qa_payload: dict[str, Any], council_payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for check in qa_payload.get("checks", []):
        if not isinstance(check, dict) or check.get("status") != "warning":
            continue
        name = str(check.get("name") or "repository_qa")
        findings.append(
            {
                "id": f"qa:{name}",
                "source": "repository_qa",
                "category": name,
                "severity": _qa_severity(name),
                "confidence": _QA_CONFIDENCE.get(name, "heuristic"),
                "title": f"{_humanise(name)} needs attention",
                "summary": str(check.get("summary") or "Repository QA reported a warning."),
                # Preserve the complete structured details. Some checks contain
                # arrays of individual paths/lines; keeping the original object
                # means the consolidated report never drops a discovered item.
                "details": check.get("details") if isinstance(check.get("details"), dict) else {},
            }
        )

    for dimension in council_payload.get("dimensions", []):
        if not isinstance(dimension, dict):
            continue
        try:
            score = float(dimension.get("score", 1.0))
        except (TypeError, ValueError):
            score = 1.0
        if score >= 0.60:
            continue
        name = str(dimension.get("dimension") or "repository_health")
        findings.append(
            {
                "id": f"council:{name}",
                "source": "repository_council",
                "category": name,
                "severity": _council_severity(score),
                "confidence": str(dimension.get("confidence") or "heuristic"),
                "title": f"Improve {_humanise(name)}",
                "summary": str(dimension.get("rationale") or "Repository Council scored this dimension below target."),
                "details": {"score": score},
            }
        )

    findings.sort(key=lambda finding: (_priority_rank(str(finding.get("severity"))), str(finding.get("id"))))
    return findings


def _summarise(
    repository_id: str,
    qa_payload: dict[str, Any],
    council_payload: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    by_severity = {severity: 0 for severity in ("critical", "high", "medium", "low")}
    by_source = {"repository_qa": 0, "repository_council": 0}
    for finding in findings:
        severity = str(finding.get("severity") or "medium")
        source = str(finding.get("source") or "")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    qa_score = float(qa_payload.get("score") or 0.0)
    council_score = float(council_payload.get("overall_score") or 0.0)
    blocking = by_severity.get("critical", 0) + by_severity.get("high", 0)
    status = "action_required" if blocking else ("review_recommended" if findings else "healthy")
    return {
        "repository_id": repository_id,
        "status": status,
        "qa_score": round(qa_score, 3),
        "council_score": round(council_score, 3),
        "finding_count": len(findings),
        "blocking_finding_count": blocking,
        "by_severity": by_severity,
        "by_source": by_source,
        "headline": (
            f"{repository_id}: {len(findings)} consolidated finding(s); "
            f"QA {qa_score:.0%}, Council {council_score:.0%}."
        ),
    }


def _prompt_details(details: object, *, max_chars: int = 3200) -> str:
    try:
        rendered = json.dumps(details, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(details)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 40] + " ... [details truncated in prompt; use report JSON for full evidence]"


def build_improvement_prompt(
    repository_id: str,
    summary: dict[str, Any],
    findings: list[dict[str, Any]],
) -> str:
    lines = [
        f"Improve the {repository_id} repository using this HIVE Repository Intelligence report as the source of truth.",
        "",
        "Business objective:",
        "Make the repository production-ready, reliable and maintainable without hiding failures or weakening existing safety, security or release gates.",
        "",
        "Required method:",
        "1. Address every finding below, highest severity first.",
        "2. Preserve public API and deployment contracts unless a finding explicitly requires a compatible migration.",
        "3. Do not silence lint/type/security findings with broad ignores, baselines or suppressions when the underlying code can be corrected.",
        "4. Add or update regression tests for every behaviour changed.",
        "5. Run the repository's native install, build, lint, type-check, test, security and smoke/release checks that are available in its CI configuration.",
        "6. Re-run HIVE Repository Intelligence after the code changes and verify the relevant findings are cleared or explicitly justified.",
        "7. Return only the changed files plus a concise validation summary, remaining risks and any deployment/environment changes required.",
        "",
        f"Current status: {summary.get('headline', '')}",
        "",
        "Consolidated findings:",
    ]
    if not findings:
        lines.append("- No QA warnings or sub-target Council dimensions were reported. Keep behaviour unchanged and verify the full native CI/release suite.")
    else:
        for index, finding in enumerate(findings, start=1):
            lines.extend(
                [
                    f"{index}. [{str(finding.get('severity', 'medium')).upper()}] "
                    f"[{finding.get('source')}] {finding.get('title')}",
                    f"   Category: {finding.get('category')}; confidence: {finding.get('confidence')}",
                    f"   Evidence: {finding.get('summary')}",
                    f"   Details: {_prompt_details(finding.get('details', {}))}",
                ]
            )
    return "\n".join(lines)


def run_repository_intelligence(settings: Settings, repository_id: str) -> dict[str, Any]:
    """Run QA + Council once, merge their evidence and persist the report."""
    occurred_at = _now_iso()
    store = D1MetadataStore(settings)

    qa_report = run_repository_qa(repository_id)
    qa_payload = qa_report.public_payload()
    append_history_entry(
        store,
        repository_id=repository_id,
        field_name="qa_history",
        entry={**qa_payload, "occurred_at": occurred_at},
    )

    warning_checks = [
        check
        for check in qa_payload.get("checks", [])
        if isinstance(check, dict) and check.get("status") == "warning"
    ]
    if warning_checks:
        append_history_entry(
            store,
            repository_id=repository_id,
            field_name="known_issues",
            entry={
                "title": "Repository Intelligence QA findings",
                "summary": f"{len(warning_checks)} QA warning check(s) detected.",
                "checks": warning_checks,
                "occurred_at": occurred_at,
            },
        )

    council_report = run_and_record_council(
        settings,
        repository_id,
        qa_payload=qa_payload,
    )
    council_payload = council_report.public_payload()

    findings = _build_findings(qa_payload, council_payload)
    summary = _summarise(repository_id, qa_payload, council_payload, findings)
    improvement_prompt = build_improvement_prompt(repository_id, summary, findings)
    dna = update_project_dna(settings, repository_id=repository_id)

    consolidated = {
        "repository_id": repository_id,
        "occurred_at": occurred_at,
        "summary": summary,
        "findings": findings,
        "improvement_prompt": improvement_prompt,
        "qa": qa_payload,
        "council": council_payload,
        "project_dna": dna,
    }

    # Persist the merged report without duplicating the already-persisted raw QA
    # and Council payloads. The response above still includes both raw reports.
    append_history_entry(
        store,
        repository_id=repository_id,
        field_name="repository_intelligence_history",
        entry={
            "repository_id": repository_id,
            "occurred_at": occurred_at,
            "summary": summary,
            "findings": findings,
            "improvement_prompt": improvement_prompt,
        },
    )
    return consolidated

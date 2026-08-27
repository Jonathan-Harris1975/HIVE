from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.services.repository_council import run_and_record_council
from app.services.repository_learning import update_project_dna
from app.services.repository_manager import RepositoryManagerError, get_repository
from app.services.repository_memory import append_history_entry
from app.services.repository_profile import build_repository_memory_profile
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




def _repository_context(repository_id: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    record = get_repository(repository_id)
    if record is None:
        raise RepositoryManagerError(f"Unknown repository_id: {repository_id}")
    root = record.workdir
    relevant_paths: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, str):
            clean = value.replace("\\", "/").strip().lstrip("/")
            if clean and (root / clean).is_file():
                relevant_paths.add(clean)
        elif isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(findings)
    top_level = sorted(path.name for path in root.iterdir())[:40]
    profile = build_repository_memory_profile(record)

    # The prompt/report context is built from the selected repository's live
    # working tree every time Intelligence runs. Do not source these fields from
    # another repository's persisted Memory record: repository identity must be
    # impossible to leak across tabs/selections.
    return {
        "repository_id": repository_id,
        "source_filename": record.manifest.source_filename,
        "fingerprint": record.manifest.fingerprint,
        "indexed_version": record.manifest.indexed_version,
        "file_count": record.manifest.file_count,
        "total_bytes": record.manifest.total_bytes,
        "languages": record.manifest.languages,
        "dependency_manifests": [
            {
                "path": dependency.manifest_path,
                "ecosystem": dependency.ecosystem,
                "declared_count": len(dependency.declared),
            }
            for dependency in record.manifest.dependencies
        ],
        "architecture": profile.get("architecture_summary", {}),
        "coding_standards": profile.get("coding_standards", {}),
        "build_profile": profile.get("build_profile", {}),
        "deployment_profile": profile.get("deployment_profile", {}),
        "environment_schema": profile.get("environment_schema", {}),
        "top_level_entries": top_level,
        "implicated_files": sorted(relevant_paths),
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
    repository_context: dict[str, Any],
) -> str:
    lines = [
        f"Repository-specific improvement plan: {repository_id}",
        f"Target snapshot: {repository_context.get('source_filename')} · fingerprint {repository_context.get('fingerprint')}",
        f"Detected language profile: {_prompt_details(repository_context.get('languages', {}), max_chars=900)}",
        f"Detected dependency manifests: {_prompt_details(repository_context.get('dependency_manifests', []), max_chars=1400)}",
        f"Detected deployment profile: {_prompt_details(repository_context.get('deployment_profile', {}), max_chars=1200)}",
        "",
        "Use the consolidated Repository Intelligence evidence below as the source of truth for this repository only.",
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
        "Repository-specific context:",
        f"- Repository ID: {repository_context.get('repository_id')}",
        f"- Snapshot: {repository_context.get('source_filename')} · fingerprint {repository_context.get('fingerprint')}",
        f"- Languages: {_prompt_details(repository_context.get('languages', {}), max_chars=900)}",
        f"- Dependency manifests: {_prompt_details(repository_context.get('dependency_manifests', []), max_chars=1400)}",
        f"- Architecture: {_prompt_details(repository_context.get('architecture', {}), max_chars=1800)}",
        f"- Coding standards: {_prompt_details(repository_context.get('coding_standards', {}), max_chars=1500)}",
        f"- Build profile: {_prompt_details(repository_context.get('build_profile', {}), max_chars=1600)}",
        f"- Deployment profile: {_prompt_details(repository_context.get('deployment_profile', {}), max_chars=1600)}",
        f"- Environment schema: {_prompt_details(repository_context.get('environment_schema', {}), max_chars=1600)}",
        f"- Top-level entries: {_prompt_details(repository_context.get('top_level_entries', []), max_chars=1200)}",
        f"- Files directly implicated by evidence: {_prompt_details(repository_context.get('implicated_files', []), max_chars=1400)}",
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


def _require_repository_payload(payload: dict[str, Any], repository_id: str, source: str) -> None:
    payload_repository_id = payload.get("repository_id")
    if payload_repository_id != repository_id:
        raise RepositoryManagerError(
            f"{source} returned repository_id {payload_repository_id!r} while {repository_id!r} was requested"
        )


def run_repository_intelligence(settings: Settings, repository_id: str) -> dict[str, Any]:
    """Run QA + Council once, merge their evidence and persist the report."""
    occurred_at = _now_iso()
    store = D1MetadataStore(settings)

    qa_report = run_repository_qa(repository_id)
    qa_payload = qa_report.public_payload()
    _require_repository_payload(qa_payload, repository_id, "Repository QA")
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
    _require_repository_payload(council_payload, repository_id, "Repository Council")

    findings = _build_findings(qa_payload, council_payload)
    summary = _summarise(repository_id, qa_payload, council_payload, findings)
    repository_context = _repository_context(repository_id, findings)
    improvement_prompt = build_improvement_prompt(
        repository_id, summary, findings, repository_context
    )
    dna = update_project_dna(settings, repository_id=repository_id)

    consolidated = {
        "repository_id": repository_id,
        "occurred_at": occurred_at,
        "summary": summary,
        "repository_context": repository_context,
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
            "repository_context": repository_context,
            "findings": findings,
            "improvement_prompt": improvement_prompt,
        },
    )
    return consolidated

"""Deterministic HIVE lifecycle report assembly and compact R2 persistence."""
from __future__ import annotations

import datetime as dt
import json
import re
import hashlib
import html
import threading
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    from app.storage.r2 import R2Storage

REPOSITORIES = (
    "AIMS", "AIMS-UI", "HIVE", "HIVE-UI", "IRS", "RAMS", "MAST", "jonathan-harris-website",
)
BUCKET = "hive-repositories"
STATUSES = {"pass", "fail", "running", "unknown", "not_applicable"}
FINAL_STATES = {"GREEN", "RECOVERING", "REVIEW_REQUIRED", "SERVICE_BLOCKED", "UNKNOWN"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WEEKLY_REPORT_LOCK = threading.Lock()


def _valid_record(value: Any, repository: str) -> bool:
    return (
        isinstance(value, dict)
        and value.get("repository") == repository
        and bool(SHA_RE.fullmatch(str(value.get("head_sha") or "")))
        and value.get("final_state") in FINAL_STATES
        and all(value.get(field) in STATUSES for field in ("ci_gate", "socket_status", "codeql_status", "gitleaks_status", "trivy_status", "codecov_status", "dast_status", "deployment_status"))
        and all(value.get(field) != "not_applicable" or bool((value.get("not_applicable_reasons") or {}).get(field)) for field in ("ci_gate", "socket_status", "codeql_status", "gitleaks_status", "trivy_status", "codecov_status", "dast_status", "deployment_status"))
        and (value.get("deployment_status") != "not_applicable" or bool(value.get("deployment_not_applicable_reason")))
        and (value.get("fallback_used") is not True or bool(value.get("fallback_reason")))
        and (value.get("kilo_triggered") is not True or bool(value.get("kilo_trigger_reason")))
        and value.get("merge_authority") == "github_native_auto_merge"
        and bool(value.get("mergify_not_applicable_reason"))
    )


def _state(record: dict[str, Any] | None) -> str:
    if not record:
        return "UNKNOWN"
    state = record.get("final_state", "UNKNOWN")
    if state not in FINAL_STATES:
        return "UNKNOWN"
    # GREEN needs affirmative, exact-revision gate and deployment evidence.
    if state == "GREEN":
        statuses = ("ci_gate", "socket_status", "codeql_status", "gitleaks_status", "trivy_status", "codecov_status", "dast_status", "deployment_status")
        raw_reasons = record.get("not_applicable_reasons")
        reasons: dict[str, Any] = raw_reasons if isinstance(raw_reasons, dict) else {}
        if any(record.get(field) not in {"pass", "not_applicable"} for field in statuses):
            return "UNKNOWN"
        if any(record.get(field) == "not_applicable" and not reasons.get(field) for field in statuses):
            return "UNKNOWN"
        if record.get("human_hold") is True:
            return "UNKNOWN"
        if record.get("deployment_status") == "pass" and (record.get("deployment_verified") is not True or record.get("deployed_sha") != record.get("head_sha")):
            return "UNKNOWN"
        if record.get("deployment_status") == "not_applicable" and not record.get("deployment_not_applicable_reason"):
            return "UNKNOWN"
    return state


def build_weekly_overview(records_by_repository: Mapping[str, Iterable[dict[str, Any]]], year: int, week: int, generated_at: str | None = None, *, current_heads: Mapping[str, str] | None = None) -> dict[str, Any]:
    observed_at = generated_at or dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    observation = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if observation.tzinfo is None:
        raise ValueError("Report observation time must include a timezone")
    repositories = []
    flattened: list[dict[str, Any]] = []
    for name in REPOSITORIES:
        valid = [item for item in records_by_repository.get(name, ()) if _valid_record(item, name)]
        current_sha = str((current_heads or {}).get(name) or "")
        current: list[tuple[dt.datetime, dict[str, Any]]] = []
        for item in valid:
            if not SHA_RE.fullmatch(current_sha) or item["head_sha"] != current_sha:
                continue
            try:
                completed = dt.datetime.fromisoformat(str(item.get("completed_at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if completed.tzinfo is None or completed > observation:
                continue
            period = completed.astimezone(dt.timezone.utc).isocalendar()
            if (period.year, period.week) == (year, week):
                current.append((completed, item))
        current.sort(key=lambda item: item[0])
        latest = current[-1][1] if current else None
        status = _state(latest)
        issue = (
            "current_head_unavailable" if not SHA_RE.fullmatch(current_sha)
            else "missing_evidence" if not valid
            else "stale_or_undated_evidence" if latest is None
            else "incomplete_evidence" if status == "UNKNOWN"
            else None
        )
        fields = ("ci_gate", "socket_status", "codeql_status", "gitleaks_status", "trivy_status", "codecov_status", "dast_status", "deployment_status")
        repositories.append({
            "repository": name, "status": status, "current_sha": current_sha or None,
            "head_observed_at": observed_at, "evidence_count": len(valid),
            "missing_evidence": latest is None, "evidence_issue": issue,
            "missing_fields": [field for field in fields if not latest or latest.get(field) in {"unknown", "running"}],
            "latest_evidence": latest,
        })
        if latest:
            flattened.append(latest)
    states = [row["status"] for row in repositories]
    overall = "SERVICE_BLOCKED" if "SERVICE_BLOCKED" in states else "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in states else "RECOVERING" if "RECOVERING" in states else "GREEN" if all(state == "GREEN" for state in states) else "UNKNOWN"
    security_failures = sum(1 for rec in flattened for key in ("socket_status", "codeql_status", "gitleaks_status", "trivy_status", "dast_status") if rec.get(key) == "fail")
    return {
        "schema_version": 1,
        "report_id": f"weekly-{year}-W{week:02d}",
        "period": {"iso_year": year, "iso_week": week},
        "generated_at": observed_at,
        "scope": "current_heads_at_observation",
        "overall_status": overall,
        "evidence_complete": len(flattened) == len(REPOSITORIES) and all(row["status"] != "UNKNOWN" and not row["missing_fields"] for row in repositories),
        "repositories": repositories,
        "metrics": {
            "prs_processed": sum(1 for rec in flattened if rec.get("pr_number") is not None),
            "autonomous_merges": sum(1 for rec in flattened if rec.get("merged") is True),
            "autofix_repairs": sum(1 for rec in flattened if rec.get("autofix_used") is True),
            "kilo_repairs": sum(1 for rec in flattened if rec.get("kilo_triggered") is True),
            "fallback_repairs": sum(1 for rec in flattened if rec.get("fallback_used") is True),
            "failed_or_exhausted_repairs": sum(1 for rec in flattened if rec.get("final_state") in {"REVIEW_REQUIRED", "SERVICE_BLOCKED"}),
            "security_blockers": security_failures,
            "deployment_failures": sum(1 for rec in flattened if rec.get("deployment_status") == "fail"),
            "human_interventions": sum(1 for rec in flattened if rec.get("human_hold") is True),
            "stale_automation_state": sum(1 for rec in flattened if rec.get("failure_class") == "stale_automation_state"),
        },
    }


def render_weekly_html(report: dict[str, Any]) -> str:
    """Render the stored report as a self-contained, script-free HTML document."""
    period = report["period"]
    title = f"HIVE weekly overview · {period['iso_year']} W{period['iso_week']:02d}"
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""), quote=True)
    repositories = []
    issues = []
    for row in report.get("repositories", []):
        repositories.append(
            "<tr><th scope=\"row\">{}</th><td><span class=\"status\">{}</span></td>"
            "<td>{}</td><td>{}</td></tr>".format(
                esc(row.get("repository")), esc(row.get("status")),
                esc(row.get("evidence_count", 0)), esc(row.get("current_sha") or "unavailable"),
            )
        )
        if row.get("evidence_issue"):
            fields = ", ".join(row.get("missing_fields", [])) or "current head or observation"
            issues.append(f"<li><strong>{esc(row.get('repository'))}</strong>: {esc(row['evidence_issue'])} ({esc(fields)})</li>")
    metrics = "".join(
        f"<li><span>{esc(key.replace('_', ' ').capitalize())}</span><strong>{esc(value)}</strong></li>"
        for key, value in report.get("metrics", {}).items()
    )
    council = "".join(
        f"<li><strong>{esc(item.get('repository'))}</strong> · {esc(item.get('status'))} · "
        f"<code>{esc(item.get('source_manifest_key'))}</code></li>"
        for item in report.get("council_evidence", [])
    )
    issue_section = f"<h2>Missing or stale evidence</h2><ul>{''.join(issues)}</ul>" if issues else ""
    council_section = f"<h2>Council evidence</h2><ul>{council}</ul>" if council else ""
    return f"""<!doctype html>
<html lang="en" data-report-id="{esc(report.get('report_id'))}" data-generated-at="{esc(report.get('generated_at'))}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>{esc(title)}</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:1120px;margin:2rem auto;padding:0 1rem;color:#172033;background:#f5f7fb}}
h1{{margin-bottom:.25rem}} .meta{{color:#526078}} .summary{{display:flex;gap:1rem;align-items:center;margin:1rem 0;padding:1rem;background:white;border:1px solid #dbe2ee;border-radius:.75rem}}
.status{{font-weight:700}} table{{width:100%;border-collapse:collapse;background:white}} th,td{{text-align:left;padding:.7rem;border-bottom:1px solid #dbe2ee;overflow-wrap:anywhere}}
th{{background:#eaf0f8}} ul{{padding-left:1.4rem}} li{{margin:.35rem 0}} code{{overflow-wrap:anywhere}} .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));list-style:none;padding:0;gap:.5rem}}
.metrics li{{display:flex;justify-content:space-between;gap:.5rem;background:white;border:1px solid #dbe2ee;border-radius:.5rem;padding:.65rem}}
</style></head><body>
<h1>{esc(title)}</h1><p class="meta">Generated {esc(report.get('generated_at'))} · Scope: {esc(report.get('scope'))}</p>
<div class="summary"><strong>Overall status: {esc(report.get('overall_status'))}</strong><span>Evidence complete: {('yes' if report.get('evidence_complete') else 'no')}</span></div>
<h2>All eight repositories</h2><table><thead><tr><th>Repository</th><th>Status</th><th>Evidence records</th><th>Current head SHA</th></tr></thead><tbody>{''.join(repositories)}</tbody></table>
{issue_section}<h2>Weekly metrics</h2><ul class="metrics">{metrics}</ul>{council_section}
</body></html>\n"""


def build_review_objects(record: dict[str, Any], year: int) -> tuple[str, dict[str, bytes]] | None:
    """Create a stable, actionable review bundle only for explicit terminal states."""
    if record.get("final_state") not in {"REVIEW_REQUIRED", "SERVICE_BLOCKED"} and record.get("human_hold") is not True:
        return None
    repository = str(record.get("repository") or "")
    sha = str(record.get("head_sha") or "")
    pr_number = record.get("pr_number")
    failure = str(record.get("failure_class") or "unclassified")
    if repository not in REPOSITORIES or not SHA_RE.fullmatch(sha):
        return None
    stable = hashlib.sha256(f"{repository}:{pr_number or 0}:{sha}:{failure}".encode()).hexdigest()[:24]
    review_id = f"review-{stable}"
    repair_prompt = record.get("repair_prompt") if isinstance(record.get("repair_prompt"), str) else None
    proposed_solution = record.get("proposed_solution") if isinstance(record.get("proposed_solution"), str) else None
    review = {
        "schema_version": 1, "review_id": review_id, "source": repository,
        "severity": "critical" if record.get("final_state") == "SERVICE_BLOCKED" else "review_required",
        "category": failure, "title": str(record.get("failed_check") or f"{repository} requires a decision"),
        "summary": str(record.get("failure_class") or "A governed automation state requires human review."),
        "requires_action": True, "action_url": f"https://github.com/{record['repository_slug']}/pull/{pr_number}" if isinstance(record.get("repository_slug"), str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", record["repository_slug"]) and isinstance(pr_number, int) and pr_number > 0 else None,
        "pr_number": pr_number, "head_sha": sha, "created_at": record.get("completed_at") or record.get("started_at"),
        "status": "open", "accept_fix_enabled": bool(isinstance(pr_number, int) and pr_number > 0 and repair_prompt and proposed_solution),
    }
    proposal = {"proposed_solution": proposed_solution, "repair_prompt": repair_prompt, "risk_impact": record.get("risk_impact"), "failed_check": record.get("failed_check")}
    lines = [f"# Review required: {repository} PR #{pr_number or 'unknown'}", "", f"- Review ID: `{review_id}`", f"- Head SHA: `{sha}`", f"- Failure class: `{failure}`", f"- Failed check: {record.get('failed_check') or 'Evidence did not identify a single failed check.'}", f"- Repair attempts: {record.get('repair_attempts', 0)}", f"- Human hold: {record.get('human_hold')}", f"- Accept Fix enabled: {review['accept_fix_enabled']}", "", "## Proposed solution", proposed_solution or "No safe proposed solution is recorded; review is required and Accept Fix remains disabled.", "", "## Repair prompt", repair_prompt or "No repair prompt is recorded.", "", "## Risk / impact", record.get("risk_impact") or "No risk statement was supplied in the evidence.", "", "## Evidence", f"`ci-reports/{repository}/.../{sha}/lifecycle/.../evidence.json`", ""]
    return review_id, {
        "review.json": (json.dumps(review, indent=2, sort_keys=True) + "\n").encode(),
        "evidence.json": (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(),
        "proposed-fix.json": (json.dumps(proposal, indent=2, sort_keys=True) + "\n").encode(),
        "review.md": "\n".join(lines).encode(),
    }


def collect_weekly_evidence(storage: R2Storage, year: int, week: int) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in REPOSITORIES}
    for repository in REPOSITORIES:
        prefix = f"ci-reports/{repository}/{year}/{week:02d}/"
        cursor = None
        scanned = 0
        while True:
            page = storage.list_objects_page(prefix=prefix, bucket=BUCKET, limit=1000, cursor=cursor, delimiter=None, max_scan_keys=50_000)
            for obj in page.objects:
                scanned += 1
                if scanned > 50_000:
                    raise RuntimeError(f"Evidence object limit exceeded for {repository}")
                key = getattr(obj, "key", "")
                if not key.endswith("/lifecycle/") and "/lifecycle/" not in key:
                    continue
                if not key.endswith("/evidence.json"):
                    continue
                try:
                    object_path = key
                    payload = storage.read_object(object_path, max_bytes=256 * 1024, bucket=BUCKET).content
                    row = json.loads(payload)
                except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                    continue
                if _valid_record(row, repository):
                    records[repository].append(row)
            cursor = page.next_cursor
            if not cursor:
                break
    return records


def _put_immutable(storage: R2Storage, key: str, body: bytes, content_type: str) -> bool:
    try:
        storage.client().put_object(Bucket=BUCKET, Key=key, Body=body, ContentType=content_type, IfNoneMatch="*")
        return True
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", "")) if isinstance(response, dict) else ""
        if code in {"PreconditionFailed", "412", "ConditionalRequestConflict"}:
            return False
        raise RuntimeError(f"R2 report write failed for {key}: {code or 'provider_error'}") from exc


def collect_council_references(storage: R2Storage, year: int, week: int) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for repository in REPOSITORIES:
        prefix = f"ci-reports/{repository}/{year}/{week:02d}/"
        cursor = None
        while True:
            page = storage.list_objects_page(prefix=prefix, bucket=BUCKET, limit=1000, cursor=cursor, delimiter=None, max_scan_keys=50_000)
            for obj in page.objects:
                key = getattr(obj, "key", "")
                if "/council/" not in key or not key.endswith("/evidence-manifest.json"):
                    continue
                try:
                    object_path = key
                    manifest = json.loads(storage.read_object(object_path, max_bytes=256 * 1024, bucket=BUCKET).content)
                except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                    continue
                run_id = str(manifest.get("workflow_run_id") or "")
                sha = str(manifest.get("default_branch_sha") or "")
                reference = {"repository": repository, "head_sha": sha, "workflow_run_id": run_id, "status": manifest.get("status", "UNKNOWN"), "source_manifest_key": key}
                references.append(reference)
            cursor = page.next_cursor
            if not cursor:
                break
    return references


def _current_repository_heads(storage: R2Storage) -> dict[str, str]:
    """Read each governed default branch through the existing GitHub credential."""
    settings = getattr(storage, "settings", None)
    token = str(getattr(settings, "github_token", "") or "").strip()
    if not settings or not token:
        return {}
    import httpx
    from app.core.governed_repositories import canonical_governed_repository_id
    from app.services.repository_refresh import github_sources

    try:
        sources = github_sources(settings)
    except ValueError:
        return {}
    from concurrent.futures import ThreadPoolExecutor

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), headers=headers) as client:
        def lookup(repository: str) -> tuple[str, str | None]:
            slug = sources.get(canonical_governed_repository_id(repository) or "")
            if not slug or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
                return repository, None
            try:
                response = client.get(f"https://api.github.com/repos/{slug}/commits", params={"per_page": 1})
                payload = response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                return repository, None
            sha = str(payload[0].get("sha") or "") if isinstance(payload, list) and payload and isinstance(payload[0], dict) else ""
            return repository, sha if SHA_RE.fullmatch(sha) else None

        with ThreadPoolExecutor(max_workers=4) as pool:
            heads = {repository: sha for repository, sha in pool.map(lookup, REPOSITORIES) if sha}
    return heads


def read_latest_weekly_report(storage: R2Storage, year: int, week: int) -> tuple[str, dict[str, Any]]:
    """Read the single current JSON report for an ISO week."""
    key = f"reports/weekly/{year}-W{week:02d}.json"
    report = json.loads(storage.read_object(key, max_bytes=2 * 1024 * 1024, bucket=BUCKET).content)
    if not isinstance(report, dict):
        raise ValueError("Weekly report must be an object")
    return key, report


def read_weekly_html(storage: R2Storage, year: int, week: int) -> str:
    """Read the HTML paired with the current JSON, rebuilding it if a write was interrupted."""
    _, report = read_latest_weekly_report(storage, year, week)
    key = f"reports/weekly/{year}-W{week:02d}.html"
    try:
        body = storage.read_object(key, max_bytes=2 * 1024 * 1024, bucket=BUCKET).content.decode("utf-8")
    except (FileNotFoundError, RuntimeError, AttributeError, UnicodeDecodeError):
        return render_weekly_html(report)
    expected = f'data-generated-at="{html.escape(str(report.get("generated_at") or ""), quote=True)}"'
    return body if expected in body else render_weekly_html(report)


def _generate_and_store_weekly_report(storage: R2Storage, year: int, week: int) -> dict[str, Any]:
    if not (2000 <= year <= 9999 and 1 <= week <= 53):
        raise ValueError("ISO year/week is out of range")
    observation = dt.datetime.now(dt.timezone.utc)
    current_heads = _current_repository_heads(storage)
    records = collect_weekly_evidence(storage, year, week)
    report = build_weekly_overview(records, year, week, observation.isoformat().replace("+00:00", "Z"), current_heads=current_heads)
    council_references = collect_council_references(storage, year, week)
    report["council_evidence"] = council_references
    report["metrics"]["council_evidence_bundles"] = len(council_references)
    json_key = f"reports/weekly/{year}-W{week:02d}.json"
    html_key = f"reports/weekly/{year}-W{week:02d}.html"
    json_body = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    html_body = render_weekly_html(report).encode()
    # HTML is written first; JSON is the commit marker used by current/history reads.
    # The process lock also serialises scheduled and operator-triggered builds.
    storage.client().put_object(Bucket=BUCKET, Key=html_key, Body=html_body, ContentType="text/html; charset=utf-8")
    storage.client().put_object(Bucket=BUCKET, Key=json_key, Body=json_body, ContentType="application/json")
    review_ids: set[str] = set()
    open_review_count = 0
    all_records = [record for group in records.values() for record in group]
    for record in all_records:
        bundle = build_review_objects(record, year)
        if not bundle:
            continue
        review_id, review_files = bundle
        if review_id in review_ids:
            continue
        review_ids.add(review_id)
        repository = record["repository"]
        pr_number = record.get("pr_number")
        started = str(record.get("completed_at") or record.get("started_at") or "")
        resolution = next((item for item in all_records if item.get("repository") == repository and item.get("pr_number") == pr_number and item is not record and str(item.get("completed_at") or item.get("started_at") or "") > started and _state(item) == "GREEN"), None)
        if resolution:
            resolved_prefix = f"reports/review-required/resolved/{year}/{review_id}"
            source_evidence = review_files["evidence.json"]
            try:
                # Keep the operator's original Accept Fix/hold decision alongside
                # the later evidence that closed the review.
                decision = storage.read_object(
                    f"reports/review-required/open/{review_id}/decision.json",
                    max_bytes=32 * 1024,
                    bucket=BUCKET,
                ).content
            except Exception:
                decision = json.dumps(
                    {"schema_version": 1, "review_id": review_id, "decision": "resolved_by_owning_system_evidence", "source": "lifecycle_evidence", "recorded_at": resolution.get("completed_at") or resolution.get("started_at")},
                    indent=2,
                    sort_keys=True,
                ).encode()
            outcome = {"schema_version": 1, "review_id": review_id, "state": "resolved", "resolution_head_sha": resolution.get("head_sha"), "merged": resolution.get("merged") is True, "deployment_verified": resolution.get("deployment_verified") is True, "resolved_at": resolution.get("completed_at") or resolution.get("started_at")}
            _put_immutable(storage, f"{resolved_prefix}/evidence.json", source_evidence, "application/json")
            _put_immutable(storage, f"{resolved_prefix}/decision.json", decision, "application/json")
            _put_immutable(storage, f"{resolved_prefix}/outcome.json", (json.dumps(outcome, indent=2, sort_keys=True) + "\n").encode(), "application/json")
            continue
        review_prefix = f"reports/review-required/open/{review_id}"
        for filename, body in review_files.items():
            _put_immutable(storage, f"{review_prefix}/{filename}", body, "text/markdown" if filename.endswith(".md") else "application/json")
        open_review_count += 1
    review_count = open_review_count
    return {"ok": True, "report": report, "r2_keys": [json_key, html_key], "objects_written_or_preserved": 2, "open_review_records": review_count}


def generate_and_store_weekly_report(storage: R2Storage, year: int, week: int) -> dict[str, Any]:
    """Serialise report builds so the weekly JSON/HTML pair cannot interleave in-process."""
    with _WEEKLY_REPORT_LOCK:
        return _generate_and_store_weekly_report(storage, year, week)


def list_open_reviews(storage: R2Storage, limit: int = 100) -> list[dict[str, Any]]:
    page = storage.list_objects_page(prefix="reports/review-required/open/", bucket=BUCKET, limit=1000, delimiter="/", max_scan_keys=20_000)
    items: list[dict[str, Any]] = []
    for prefix in page.prefixes[:max(1, min(limit, 500))]:
        review_path = f"{prefix.rstrip('/')}/review.json"
        try:
            payload = json.loads(storage.read_object(review_path, max_bytes=32 * 1024, bucket=BUCKET).content)
        except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("status") == "open":
            current_year = dt.datetime.now(dt.timezone.utc).isocalendar().year
            resolved = False
            for resolved_year in (current_year, current_year - 1, current_year - 2):
                try:
                    storage.read_object(f"reports/review-required/resolved/{resolved_year}/{payload.get('review_id')}/outcome.json", max_bytes=32 * 1024, bucket=BUCKET)
                    resolved = True
                    break
                except (FileNotFoundError, RuntimeError, ValueError):
                    continue
            if resolved:
                continue
            try:
                proposal = json.loads(storage.read_object(f"{prefix.rstrip('/')}/proposed-fix.json", max_bytes=64 * 1024, bucket=BUCKET).content)
                payload["proposal"] = proposal
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                payload["proposal"] = {}
            try:
                evidence = json.loads(storage.read_object(f"{prefix.rstrip('/')}/evidence.json", max_bytes=256 * 1024, bucket=BUCKET).content)
                payload["evidence"] = {key: evidence.get(key) for key in ("failed_check", "failure_class", "repair_attempts", "human_hold", "started_at", "completed_at", "source_workflow", "source_workflow_conclusion")}
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                payload["evidence"] = {}
            try:
                decision = json.loads(storage.read_object(f"{prefix.rstrip('/')}/decision.json", max_bytes=32 * 1024, bucket=BUCKET).content)
                payload["decision_recorded"] = decision.get("decision") == "accept_fix"
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                payload["decision_recorded"] = False
            try:
                outcome = json.loads(storage.read_object(f"{prefix.rstrip('/')}/outcome.json", max_bytes=32 * 1024, bucket=BUCKET).content)
                payload["dispatch_status"] = outcome.get("dispatch")
            except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
                payload["dispatch_status"] = "not_started"
            items.append(payload)
    return items


def accept_review_fix(storage: R2Storage, settings: Any, review_id: str) -> dict[str, Any]:
    """Record an operator decision, revalidate the PR head, then dispatch the trusted repair workflow."""
    import datetime as dt
    import httpx
    from app.core.governed_repositories import canonical_governed_repository_id
    from app.services.repository_refresh import github_sources

    if not re.fullmatch(r"review-[0-9a-f]{24}", review_id):
        raise ValueError("Invalid review id")
    prefix = f"reports/review-required/open/{review_id}"
    try:
        review = json.loads(storage.read_object(f"{prefix}/review.json", max_bytes=64 * 1024, bucket=BUCKET).content)
        evidence = json.loads(storage.read_object(f"{prefix}/evidence.json", max_bytes=256 * 1024, bucket=BUCKET).content)
        proposed = json.loads(storage.read_object(f"{prefix}/proposed-fix.json", max_bytes=64 * 1024, bucket=BUCKET).content)
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise LookupError("Review record is unavailable") from exc
    if not isinstance(review, dict) or review.get("review_id") != review_id or review.get("status") != "open":
        raise LookupError("Review record is not open")
    if not isinstance(evidence, dict) or evidence.get("head_sha") != review.get("head_sha") or evidence.get("repository") != review.get("source") or evidence.get("pr_number") != review.get("pr_number"):
        raise ValueError("Review evidence does not match the decision target")
    current_year = dt.datetime.now(dt.timezone.utc).isocalendar().year
    for resolved_year in (current_year, current_year - 1, current_year - 2):
        try:
            storage.read_object(f"reports/review-required/resolved/{resolved_year}/{review_id}/outcome.json", max_bytes=32 * 1024, bucket=BUCKET)
            raise ValueError("This review is already resolved by owning-system evidence")
        except FileNotFoundError:
            continue
    if not isinstance(proposed, dict) or not review.get("accept_fix_enabled") or not proposed.get("proposed_solution") or not proposed.get("repair_prompt"):
        raise ValueError("Accept Fix is disabled until a safe proposal and repair prompt are recorded")
    repair_prompt = str(proposed["repair_prompt"]).strip()
    if len(repair_prompt) < 30 or len(repair_prompt) > 3500:
        raise ValueError("Repair prompt must be between 30 and 3,500 characters")
    repo_id = canonical_governed_repository_id(str(review.get("source") or ""))
    sources = github_sources(settings)
    slug = sources.get(repo_id or "")
    pr_number = review.get("pr_number")
    head_sha = str(review.get("head_sha") or "")
    if not slug or not isinstance(pr_number, int) or not SHA_RE.fullmatch(head_sha):
        raise ValueError("Review is missing a governed repository, PR number or exact head SHA")
    token = str(settings.github_token or "").strip()
    if not token:
        raise RuntimeError("GitHub repair dispatch is not configured")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    base = "https://api.github.com"
    with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), headers=headers) as client:
        repository = client.get(f"{base}/repos/{slug}")
        if repository.status_code != 200:
            raise RuntimeError(f"GitHub repository verification failed with HTTP {repository.status_code}")
        default_branch = str(repository.json().get("default_branch") or "")
        if not default_branch or default_branch.startswith("-"):
            raise RuntimeError("GitHub repository default branch could not be verified")
        pull = client.get(f"{base}/repos/{slug}/pulls/{pr_number}")
        if pull.status_code != 200:
            raise RuntimeError(f"GitHub pull request verification failed with HTTP {pull.status_code}")
        pull_payload = pull.json()
        if pull_payload.get("state") != "open" or pull_payload.get("merged") is True or pull_payload.get("head", {}).get("sha") != head_sha:
            raise ValueError("Pull request is closed or its exact head moved; refresh the evidence before accepting")
        decision = {"schema_version": 1, "review_id": review_id, "decision": "accept_fix", "decided_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "actor": "authenticated_hive_operator", "repository": slug, "pr_number": pr_number, "head_sha": head_sha, "dispatch": "pending"}
        decision_body = (json.dumps(decision, indent=2, sort_keys=True) + "\n").encode()
        if not _put_immutable(storage, f"{prefix}/decision.json", decision_body, "application/json"):
            return {"ok": True, "accepted": True, "already_recorded": True, "review_id": review_id, "dispatch": "not_retried_to_prevent_duplicate"}
        try:
            response = client.post(f"{base}/repos/{slug}/actions/workflows/pr-issue-repair.yml/dispatches", json={"ref": default_branch, "inputs": {"pr_number": str(pr_number), "review_id": review_id, "source_head_sha": head_sha, "repair_prompt": repair_prompt}})
            if response.status_code != 204:
                outcome = {"schema_version": 1, "review_id": review_id, "dispatch": "rejected", "http_status": response.status_code, "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
                _put_immutable(storage, f"{prefix}/outcome.json", (json.dumps(outcome, indent=2, sort_keys=True) + "\n").encode(), "application/json")
                return {"ok": False, "accepted": True, "review_id": review_id, "dispatch": "rejected", "http_status": response.status_code}
        except httpx.HTTPError:
            outcome = {"schema_version": 1, "review_id": review_id, "dispatch": "unknown", "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "reason": "provider_response_uncertain"}
            _put_immutable(storage, f"{prefix}/outcome.json", (json.dumps(outcome, indent=2, sort_keys=True) + "\n").encode(), "application/json")
            return {"ok": False, "accepted": True, "review_id": review_id, "dispatch": "unknown", "retry": False}
    outcome = {"schema_version": 1, "review_id": review_id, "dispatch": "accepted", "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
    _put_immutable(storage, f"{prefix}/outcome.json", (json.dumps(outcome, indent=2, sort_keys=True) + "\n").encode(), "application/json")
    return {"ok": True, "accepted": True, "review_id": review_id, "dispatch": "accepted", "workflow": "pr-issue-repair.yml", "merge_authority": "github_native_auto_merge"}


def list_weekly_reports(storage: R2Storage, limit: int = 12) -> list[dict[str, Any]]:
    """List compact report summaries from the flat JSON/HTML weekly pairs."""
    limit = max(1, min(int(limit), 52))
    cursor = None
    keys: set[tuple[int, int]] = set()
    summaries: list[dict[str, Any]] = []
    while True:
        page = storage.list_objects_page(prefix="reports/weekly/", bucket=BUCKET, limit=1000, cursor=cursor, delimiter=None, max_scan_keys=5000)
        for obj in page.objects:
            key = str(getattr(obj, "key", ""))
            match = re.fullmatch(r"reports/weekly/(\d{4})-W(\d{2})\.json", key)
            if match:
                keys.add((int(match.group(1)), int(match.group(2))))
        cursor = page.next_cursor
        if not cursor:
            break
    for year, week in keys:
        try:
            key, report = read_latest_weekly_report(storage, year, week)
        except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(report.get("period"), dict):
            summaries.append({"report_id": report.get("report_id"), "period": report["period"], "overall_status": report.get("overall_status", "UNKNOWN"), "evidence_complete": report.get("evidence_complete") is True, "r2_key": key, "html_r2_key": key[:-5] + ".html"})
    summaries.sort(key=lambda item: (int(item.get("period", {}).get("iso_year", 0)), int(item.get("period", {}).get("iso_week", 0))), reverse=True)
    return summaries[:limit]


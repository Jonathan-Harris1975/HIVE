import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.lifecycle_reports import (
    BUCKET,
    REPOSITORIES,
    _put_immutable,
    _recovered_failed_check,
    build_review_objects,
    build_weekly_overview,
    collect_council_references,
    collect_weekly_evidence,
    generate_and_store_weekly_report,
    list_weekly_reports,
    verified_open_review_snapshot,
    render_weekly_html,
)


def row(repository: str, **overrides):
    base = {
        "repository": repository,
        "head_sha": "a" * 40,
        "final_state": "GREEN",
        "ci_gate": "pass",
        "socket_status": "pass",
        "codeql_status": "pass",
        "gitleaks_status": "pass",
        "trivy_status": "pass",
        "codecov_status": "pass",
        "dast_status": "pass",
        "deployment_status": "pass",
        "deployment_verified": True,
        "deployed_sha": "a" * 40,
        "not_applicable_reasons": {},
        "merge_authority": "github_native_auto_merge",
        "completed_at": "2026-10-12T07:00:00Z",
    }
    return {**base, **overrides}


class LifecycleReportTests(unittest.TestCase):
    def test_missing_repository_evidence_never_produces_green(self):
        inputs = {repo: [row(repo)] for repo in REPOSITORIES[:-1]}
        report = build_weekly_overview(inputs, 2026, 42, current_heads={repo: "a" * 40 for repo in REPOSITORIES})
        self.assertEqual(report["overall_status"], "UNKNOWN")
        self.assertFalse(report["evidence_complete"])
        self.assertEqual(len(report["repositories"]), 8)
        self.assertTrue(report["repositories"][-1]["missing_evidence"])

    def test_green_requires_affirmative_gate_and_deployment_evidence(self):
        inputs = {repo: [row(repo)] for repo in REPOSITORIES}
        self.assertEqual(build_weekly_overview(inputs, 2026, 42, current_heads={repo: "a" * 40 for repo in REPOSITORIES})["overall_status"], "GREEN")
        inputs["HIVE"] = [row("HIVE", ci_gate="unknown")]
        self.assertEqual(build_weekly_overview(inputs, 2026, 42, current_heads={repo: "a" * 40 for repo in REPOSITORIES})["overall_status"], "UNKNOWN")
        inputs["HIVE"] = [row("HIVE", final_state="SERVICE_BLOCKED", ci_gate="unknown", trivy_status="fail")]
        blocked = build_weekly_overview(inputs, 2026, 42, current_heads={repo: "a" * 40 for repo in REPOSITORIES})
        self.assertEqual(blocked["overall_status"], "SERVICE_BLOCKED")
        self.assertFalse(blocked["evidence_complete"])

    def test_review_items_are_created_only_for_terminal_human_decisions(self):
        candidate = row("HIVE", final_state="REVIEW_REQUIRED", failure_class="repair_exhausted", pr_number=22, proposed_solution="Fix the verified timeout handling.", repair_prompt="Inspect and patch the retry timeout.")
        review_id, bundle = build_review_objects(candidate, 2026)
        self.assertTrue(review_id.startswith("review-"))
        self.assertIn("evidence.json", bundle)
        review = json.loads(bundle["review.json"])
        self.assertFalse(review["accept_fix_enabled"])
        self.assertEqual(review["repair_dispatch_owner"], "AIMS")
        self.assertIn(b"Repair dispatch owner: AIMS", bundle["review.md"])
        routine = row("HIVE", final_state="GREEN")
        self.assertIsNone(build_review_objects(routine, 2026))

    def test_plain_ci_failures_are_operational_not_human_reviews(self):
        failed = row("HIVE", final_state="SERVICE_BLOCKED", ci_gate="fail",
                     failure_class="check_failed", failed_check="ci_gate",
                     pr_number=None, repair_attempts=0, human_hold=None)
        self.assertIsNone(build_review_objects(failed, 2026))
        self.assertIsNotNone(build_review_objects(failed, 2026, include_operational=True))
        held = {**failed, "human_hold": True}
        self.assertIsNotNone(build_review_objects(held, 2026))
        exhausted = {**failed, "failure_class": "repair_exhausted"}
        self.assertIsNotNone(build_review_objects(exhausted, 2026))
        proposed = {**failed, "pr_number": 17, "proposed_solution": "Repair gate",
                    "repair_prompt": "Fix the verified check failure"}
        self.assertIsNotNone(build_review_objects(proposed, 2026))

    def test_recovery_of_exact_failed_check_does_not_claim_green(self):
        original = row("HIVE", head_sha="a" * 40, final_state="SERVICE_BLOCKED",
                       ci_gate="fail", failed_check="ci_gate", pr_number=None,
                       completed_at="2026-11-16T18:30:00Z")
        later = row("HIVE", head_sha="b" * 40, deployed_sha="b" * 40,
                    final_state="UNKNOWN", codecov_status="unknown",
                    pr_number=None, source_workflow_conclusion="success",
                    completed_at="2026-11-18T23:21:00Z")
        self.assertTrue(_recovered_failed_check(original, later))
        self.assertFalse(_recovered_failed_check(original, {**later, "ci_gate": "unknown"}))
        self.assertFalse(_recovered_failed_check(original, {**later, "deployment_verified": False}))
        self.assertFalse(_recovered_failed_check(original, {**later, "deployed_sha": "a" * 40}))
        self.assertFalse(_recovered_failed_check(original, {**later, "source_workflow_conclusion": "skipped"}))
        self.assertFalse(_recovered_failed_check({**original, "human_hold": True}, later))
        self.assertFalse(_recovered_failed_check({**original, "failed_check": "unclassified"}, later))
        self.assertFalse(_recovered_failed_check(original, {**later, "completed_at": "2026-11-15T00:00:00Z"}))
        security = {**original, "failed_check": "Trivy, Gitleaks and actionlint"}
        self.assertTrue(_recovered_failed_check(security, later))
        self.assertFalse(_recovered_failed_check(security, {**later, "gitleaks_status": "unknown"}))

    def test_legacy_nonactionable_review_is_not_listed(self):
        from app.services.lifecycle_reports import list_open_reviews

        legacy = row("HIVE", final_state="SERVICE_BLOCKED", ci_gate="fail",
                     failure_class="check_failed", failed_check="ci_gate",
                     pr_number=None, repair_attempts=0, human_hold=None)
        held = {**legacy, "human_hold": True}
        records = {"review-legacy": legacy, "review-held": held}

        class Storage:
            def list_objects_page(self, **_kwargs):
                return SimpleNamespace(
                    prefixes=[f"reports/review-required/open/{key}/" for key in records],
                )

            def read_object(self, key, **_kwargs):
                review_id = key.split("/")[3]
                if key.endswith("/review.json"):
                    return SimpleNamespace(content=json.dumps({
                        "review_id": review_id, "status": "open", "source": "HIVE",
                    }).encode())
                if key.endswith("/evidence.json"):
                    return SimpleNamespace(content=json.dumps(records[review_id]).encode())
                if "/resolved/" in key or key.endswith("/decision.json") or key.endswith("/outcome.json"):
                    raise FileNotFoundError(key)
                return SimpleNamespace(content=b"{}")

        listed = list_open_reviews(Storage())
        self.assertEqual([item["review_id"] for item in listed], ["review-held"])

    def test_verified_review_snapshot_never_promotes_unverified_heads(self):
        candidate = {"review_id": "review-current", "source": "HIVE", "pr_number": 7,
                     "head_sha": "a" * 40, "title": "Human hold"}
        moved = {"review_id": "review-superseded", "source": "AIMS", "pr_number": None,
                 "head_sha": "b" * 40, "title": "Old hold"}
        unavailable = {"review_id": "review-unknown", "source": "HIVE-UI", "pr_number": None,
                       "head_sha": "c" * 40, "title": "Unverified hold"}
        with (
            patch("app.services.lifecycle_reports.list_open_reviews", return_value=[candidate, moved, unavailable]),
            patch("app.services.lifecycle_reports._current_review_source_heads",
                  return_value={("HIVE", 7): "a" * 40, ("AIMS", None): "d" * 40}),
        ):
            snapshot = verified_open_review_snapshot(object())
        self.assertEqual(snapshot["verification_status"], "unknown")
        self.assertEqual(snapshot["scope"], "persisted_human_review_records")
        self.assertEqual([x["review_id"] for x in snapshot["items"]], ["review-current"])
        self.assertEqual(snapshot["items"][0]["source_verification"], "exact_head_match")
        self.assertEqual([x["review_id"] for x in snapshot["unverified_items"]],
                         ["review-superseded", "review-unknown"])
        self.assertEqual([x["reason"] for x in snapshot["unverified_items"]],
                         ["source_head_mismatch", "source_head_unavailable"])
        self.assertEqual(snapshot["unverified_count"], 2)
        self.assertTrue(snapshot["observed_at"].endswith("Z"))

    def test_fork_review_uses_governed_base_default_branch_head(self):
        from app.services.lifecycle_reports import _current_review_source_heads

        class Client:
            def __init__(self, *, headers, timeout):
                self.responses = [
                    SimpleNamespace(status_code=200, json=lambda: {
                        "state": "open",
                        "base": {"repo": {"full_name": "owner/HIVE"}},
                        "head": {"repo": {"full_name": "contributor/HIVE"}, "sha": "b" * 40},
                    }),
                    SimpleNamespace(status_code=200, json=lambda: [{"sha": "a" * 40}]),
                ]
                self.paths = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get(self, path, params=None):
                self.paths.append((path, params))
                return self.responses.pop(0)

        client = Client(headers={}, timeout=None)
        settings = SimpleNamespace(github_token="token")
        storage = SimpleNamespace(settings=settings)
        with (
            patch("httpx.Client", return_value=client),
            patch("app.core.governed_repositories.canonical_governed_repository_id", return_value="HIVE"),
            patch("app.services.repository_refresh.github_sources", return_value={"HIVE": "owner/HIVE"}),
        ):
            heads = _current_review_source_heads(storage, [{"source": "HIVE", "pr_number": 7}])

        self.assertEqual(heads, {("HIVE", 7): "a" * 40})
        self.assertEqual(client.paths[-1], ("https://api.github.com/repos/owner/HIVE/commits", {"per_page": 1}))

    def test_review_with_unexpected_base_repository_is_unavailable(self):
        from app.services.lifecycle_reports import _current_review_source_heads

        class Client:
            def __init__(self, *, headers, timeout):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get(self, _path, params=None):
                return SimpleNamespace(status_code=200, json=lambda: {
                    "state": "open",
                    "base": {"repo": {"full_name": "attacker/OTHER"}},
                    "head": {"repo": {"full_name": "contributor/HIVE"}, "sha": "a" * 40},
                })

        storage = SimpleNamespace(settings=SimpleNamespace(github_token="token"))
        with (
            patch("httpx.Client", return_value=Client()),
            patch("app.core.governed_repositories.canonical_governed_repository_id", return_value="HIVE"),
            patch("app.services.repository_refresh.github_sources", return_value={"HIVE": "owner/HIVE"}),
        ):
            heads = _current_review_source_heads(storage, [{"source": "HIVE", "pr_number": 7}])

        self.assertEqual(heads, {})

    def test_review_inventory_fail_closed_when_paginated_or_corrupt(self):
        from app.services.lifecycle_reports import list_open_reviews

        class IncompleteStorage:
            def list_objects_page(self, **_kwargs):
                return SimpleNamespace(prefixes=["reports/review-required/open/review-1/"], next_cursor="more")

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            list_open_reviews(IncompleteStorage())

        class BrokenStorage:
            def list_objects_page(self, **_kwargs):
                return SimpleNamespace(prefixes=["reports/review-required/open/review-1/"], next_cursor=None)

            def read_object(self, *_args, **_kwargs):
                raise RuntimeError("R2 upstream error")

        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            list_open_reviews(BrokenStorage())

    def test_empty_review_snapshot_is_only_a_persisted_record_claim(self):
        with (
            patch("app.services.lifecycle_reports.list_open_reviews", return_value=[]),
            patch("app.services.lifecycle_reports._current_review_source_heads") as lookup,
        ):
            snapshot = verified_open_review_snapshot(object())
        self.assertEqual(snapshot["verification_status"], "verified")
        self.assertEqual(snapshot["count"], 0)
        self.assertEqual(snapshot["scope"], "persisted_human_review_records")
        lookup.assert_not_called()

    def test_html_report_lists_all_eight_repositories_and_escapes_evidence(self):
        report = build_weekly_overview({}, 2026, 42)
        report["repositories"][0]["evidence_issue"] = "<script>alert(1)</script>"
        rendered = render_weekly_html(report)
        self.assertIn("All eight repositories", rendered)
        self.assertTrue(all(repository in rendered for repository in REPOSITORIES))
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

    def test_weekly_evidence_scans_only_valid_lifecycle_objects(self):
        valid = row("AIMS")
        page = SimpleNamespace(
            objects=[
                SimpleNamespace(key="ci-reports/AIMS/2026/42/head/lifecycle/evidence.json"),
                SimpleNamespace(key="ci-reports/AIMS/2026/42/head/summary.json"),
            ],
            next_cursor=None,
        )

        class Storage:
            def list_objects_page(self, **_kwargs):
                return page

            def read_object(self, key, **_kwargs):
                content = json.dumps(valid).encode() if "/AIMS/" in key else b"{}"
                return SimpleNamespace(content=content)

        records = collect_weekly_evidence(Storage(), 2026, 42)
        self.assertEqual(records["AIMS"], [valid])
        self.assertEqual(records["HIVE"], [])

    def test_immutable_write_reports_collision_without_overwriting(self):
        class Storage:
            def __init__(self, error=None):
                self.error = error
                self.writes = []

            def client(self):
                storage = self

                class Client:
                    def put_object(self, **kwargs):
                        storage.writes.append(kwargs)
                        if storage.error:
                            raise storage.error

                return Client()

        ok = Storage()
        self.assertTrue(_put_immutable(ok, "reports/x.json", b"{}", "application/json"))
        self.assertEqual(ok.writes[0]["Bucket"], BUCKET)
        class WriteConflict(Exception):
            response = {"Error": {"Code": "PreconditionFailed"}}

        conflict = WriteConflict("already exists")
        duplicate = Storage(conflict)
        self.assertFalse(_put_immutable(duplicate, "reports/x.json", b"{}", "application/json"))

    def test_council_manifests_are_referenced_without_creating_extra_report_objects(self):
        manifest_key = "ci-reports/HIVE/2026/42/council/run/evidence-manifest.json"
        manifest = {"workflow_run_id": "run-1", "default_branch_sha": "a" * 40, "status": "PASS"}

        class Storage:
            def __init__(self):
                self.puts = []

            def list_objects_page(self, **kwargs):
                objects = [SimpleNamespace(key=manifest_key)] if kwargs["prefix"].startswith("ci-reports/HIVE/") else []
                return SimpleNamespace(objects=objects, next_cursor=None)

            def read_object(self, *_args, **_kwargs):
                return SimpleNamespace(content=json.dumps(manifest).encode())

            def client(self):
                storage = self

                class Client:
                    def put_object(self, **kwargs):
                        storage.puts.append(kwargs)

                return Client()

        storage = Storage()
        refs = collect_council_references(storage, 2026, 42)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["workflow_run_id"], "run-1")
        self.assertEqual(storage.puts, [])

    def test_report_generation_retains_source_evidence_for_governed_housekeeping(self):
        class Storage:
            def __init__(self):
                self.puts = []
                self.deleted = []

            def client(self):
                storage = self

                class Client:
                    def put_object(self, **kwargs):
                        storage.puts.append(kwargs)

                return Client()

            def list_objects_page(self, *, prefix, **_kwargs):
                return SimpleNamespace(
                    objects=[SimpleNamespace(key=f"{prefix}run/evidence.json")],
                    prefixes=[],
                    next_cursor=None,
                    scanned_count=1,
                )

            def delete_objects(self, keys, *, bucket):
                self.deleted.extend(keys)
                return {"ok": True, "requested_count": len(keys), "deleted_count": len(keys)}

        storage = Storage()
        heads = {repo: "a" * 40 for repo in REPOSITORIES}
        records = {repo: [row(repo)] for repo in REPOSITORIES}
        with self.assertRaises(ValueError):
            generate_and_store_weekly_report(storage, 1999, 0)
        with (
            patch("app.services.lifecycle_reports._current_repository_heads", return_value=heads),
            patch("app.services.lifecycle_reports.collect_weekly_evidence", return_value=records),
            patch("app.services.lifecycle_reports.collect_council_references", return_value=[]),
        ):
            result = generate_and_store_weekly_report(storage, 2026, 42)
        self.assertTrue(result["ok"])
        self.assertEqual(result["report"]["overall_status"], "GREEN")
        self.assertEqual(result["objects_written_or_preserved"], 9)
        self.assertEqual(
            [item["Key"] for item in storage.puts],
            [f"reports/weekly/2026-W42-{repo}.html" for repo in REPOSITORIES]
            + ["reports/weekly/2026-W42.json"],
        )
        self.assertEqual(
            [item["ContentType"] for item in storage.puts],
            ["text/html; charset=utf-8"] * 8 + ["application/json"],
        )
        self.assertEqual(len(storage.puts), 9)
        self.assertEqual(set(result["repository_report_keys"]), set(REPOSITORIES))
        self.assertEqual(len(result["report"]["repositories"]), 8)
        self.assertFalse(result["cleanup_complete"])
        self.assertEqual(result["retention_owner"], "storage-housekeeping-weekly")
        self.assertEqual(result["deleted_evidence_objects"], 0)
        self.assertEqual(storage.deleted, [])

        storage.puts.clear()
        storage.deleted.clear()
        with (
            patch("app.services.lifecycle_reports._current_repository_heads", return_value=heads),
            patch("app.services.lifecycle_reports.collect_weekly_evidence", return_value=records),
            patch("app.services.lifecycle_reports.collect_council_references", return_value=[]),
        ):
            restored = generate_and_store_weekly_report(storage, 2026, 42, preserve_evidence=True)
        self.assertEqual(len(storage.puts), 9)
        self.assertEqual(storage.deleted, [])
        self.assertEqual(restored["deleted_evidence_objects"], 0)
        self.assertFalse(restored["cleanup_complete"])

    def test_weekly_report_index_reads_flat_json_pairs_and_skips_bad_documents(self):
        class Storage:
            def list_objects_page(self, *, prefix, **_kwargs):
                keys = [
                    "reports/weekly/2026-W41.json", "reports/weekly/2026-W41.html",
                    "reports/weekly/2026-W42.json", "reports/weekly/2026-W42.html",
                    "reports/weekly/2026/42/snapshots/old.json",
                ]
                return SimpleNamespace(objects=[SimpleNamespace(key=key) for key in keys], prefixes=[], next_cursor=None)

            def read_object(self, key, **_kwargs):
                if "W41" in key:
                    raise ValueError("bad report")
                return SimpleNamespace(
                    content=json.dumps(
                        {"report_id": "weekly-2026-W42", "period": {"iso_year": 2026, "iso_week": 42}, "overall_status": "UNKNOWN"}
                    ).encode()
                )

        reports = list_weekly_reports(Storage())
        self.assertEqual([item["report_id"] for item in reports], ["weekly-2026-W42"])
        self.assertEqual(reports[0]["r2_key"], "reports/weekly/2026-W42.json")
        self.assertEqual(reports[0]["html_r2_key"], "reports/weekly/2026-W42.html")

    def test_weekly_history_keeps_nested_legacy_reports_as_unknown(self):
        old_report = {
            "report_id": "weekly-2026-W41",
            "period": {"iso_year": 2026, "iso_week": 41},
            "overall_status": "GREEN",
            "evidence_complete": True,
            "repositories": [{"repository": name, "status": "GREEN"} for name in REPOSITORIES],
        }

        class Storage:
            def list_objects_page(self, *, prefix, **_kwargs):
                if prefix == "reports/weekly/":
                    return SimpleNamespace(objects=[], prefixes=["reports/weekly/2026/"], next_cursor=None)
                if prefix == "reports/weekly/2026/":
                    return SimpleNamespace(objects=[], prefixes=["reports/weekly/2026/41/"], next_cursor=None)
                return SimpleNamespace(objects=[], prefixes=[], next_cursor=None)

            def read_object(self, key, **_kwargs):
                if key == "reports/weekly/2026/41/overview.json":
                    return SimpleNamespace(content=json.dumps(old_report).encode())
                raise FileNotFoundError(key)

        reports = list_weekly_reports(Storage())
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["report_id"], "weekly-2026-W41")
        self.assertEqual(reports[0]["overall_status"], "UNKNOWN")
        self.assertFalse(reports[0]["evidence_complete"])
        self.assertNotIn("html_r2_key", reports[0])

if __name__ == "__main__":
    unittest.main()

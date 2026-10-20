import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.lifecycle_reports import (
    BUCKET,
    REPOSITORIES,
    _put_immutable,
    build_review_objects,
    build_weekly_overview,
    collect_council_references,
    collect_weekly_evidence,
    generate_and_store_weekly_report,
    list_weekly_reports,
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
        "mergify_not_applicable_reason": "GitHub native auto-merge is current.",
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
        self.assertIn(b"Accept Fix enabled: True", bundle["review.md"])
        routine = row("HIVE", final_state="GREEN")
        self.assertIsNone(build_review_objects(routine, 2026))

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

    def test_report_generation_writes_one_flat_json_and_one_html_for_all_repositories(self):
        class Storage:
            def __init__(self):
                self.puts = []

            def client(self):
                storage = self

                class Client:
                    def put_object(self, **kwargs):
                        storage.puts.append(kwargs)

                return Client()

        storage = Storage()
        with self.assertRaises(ValueError):
            generate_and_store_weekly_report(storage, 1999, 0)
        with (
            patch("app.services.lifecycle_reports.collect_weekly_evidence", return_value={}),
            patch("app.services.lifecycle_reports.collect_council_references", return_value=[]),
        ):
            result = generate_and_store_weekly_report(storage, 2026, 42)
        self.assertTrue(result["ok"])
        self.assertEqual(result["report"]["overall_status"], "UNKNOWN")
        self.assertEqual(result["objects_written_or_preserved"], 2)
        self.assertEqual([item["Key"] for item in storage.puts], [
            "reports/weekly/2026-W42.html", "reports/weekly/2026-W42.json",
        ])
        self.assertEqual([item["ContentType"] for item in storage.puts], [
            "text/html; charset=utf-8", "application/json",
        ])
        self.assertEqual(len(storage.puts), 2)
        self.assertEqual(len(result["report"]["repositories"]), 8)

    def test_weekly_report_index_reads_flat_json_pairs_and_skips_bad_documents(self):
        class Storage:
            def list_objects_page(self, *, prefix, **_kwargs):
                keys = [
                    "reports/weekly/2026-W41.json", "reports/weekly/2026-W41.html",
                    "reports/weekly/2026-W42.json", "reports/weekly/2026-W42.html",
                    "reports/weekly/2026/42/snapshots/old.json",
                ]
                return SimpleNamespace(objects=[SimpleNamespace(key=key) for key in keys], next_cursor=None)

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

if __name__ == "__main__":
    unittest.main()


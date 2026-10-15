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
    render_weekly_markdown,
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
        report = build_weekly_overview(inputs, 2026, 42)
        self.assertEqual(report["overall_status"], "UNKNOWN")
        self.assertFalse(report["evidence_complete"])
        self.assertEqual(len(report["repositories"]), 8)
        self.assertTrue(report["repositories"][-1]["missing_evidence"])

    def test_green_requires_affirmative_gate_and_deployment_evidence(self):
        inputs = {repo: [row(repo)] for repo in REPOSITORIES}
        self.assertEqual(build_weekly_overview(inputs, 2026, 42)["overall_status"], "GREEN")
        inputs["HIVE"] = [row("HIVE", ci_gate="unknown")]
        self.assertEqual(build_weekly_overview(inputs, 2026, 42)["overall_status"], "UNKNOWN")

    def test_review_items_are_created_only_for_terminal_human_decisions(self):
        candidate = row("HIVE", final_state="REVIEW_REQUIRED", failure_class="repair_exhausted", pr_number=22, proposed_solution="Fix the verified timeout handling.", repair_prompt="Inspect and patch the retry timeout.")
        review_id, bundle = build_review_objects(candidate, 2026)
        self.assertTrue(review_id.startswith("review-"))
        self.assertIn("evidence.json", bundle)
        self.assertIn(b"Accept Fix enabled: True", bundle["review.md"])
        routine = row("HIVE", final_state="GREEN")
        self.assertIsNone(build_review_objects(routine, 2026))

    def test_markdown_is_compact_and_lists_every_repository(self):
        report = build_weekly_overview({}, 2026, 42)
        markdown = render_weekly_markdown(report)
        self.assertIn("Status: UNKNOWN", markdown)
        self.assertTrue(all(f"| {repository} | UNKNOWN |" in markdown for repository in REPOSITORIES))

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

    def test_council_manifest_is_indexed_immutably(self):
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
        self.assertEqual(storage.puts[0]["Bucket"], BUCKET)

    def test_report_generation_validates_period_and_writes_immutable_overviews(self):
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
        self.assertGreaterEqual(result["objects_written_or_preserved"], 10)
        self.assertEqual(len(storage.puts), result["objects_written_or_preserved"])

    def test_weekly_report_index_is_newest_first_and_skips_bad_documents(self):
        class Storage:
            def list_objects_page(self, *, prefix, **_kwargs):
                if prefix == "reports/weekly/":
                    return SimpleNamespace(prefixes=["reports/weekly/2026/"], objects=[], next_cursor=None)
                return SimpleNamespace(
                    prefixes=["reports/weekly/2026/41/", "reports/weekly/2026/42/"],
                    objects=[],
                    next_cursor=None,
                )

            def read_object(self, key, **_kwargs):
                if "/41/" in key:
                    raise ValueError("bad report")
                return SimpleNamespace(
                    content=json.dumps(
                        {"report_id": "weekly-2026-W42", "period": {"iso_year": 2026, "iso_week": 42}, "overall_status": "UNKNOWN"}
                    ).encode()
                )

        reports = list_weekly_reports(Storage())
        self.assertEqual([item["report_id"] for item in reports], ["weekly-2026-W42"])

if __name__ == "__main__":
    unittest.main()

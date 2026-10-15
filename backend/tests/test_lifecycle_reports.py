import unittest
from app.services.lifecycle_reports import REPOSITORIES, build_review_objects, build_weekly_overview, render_weekly_markdown


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

if __name__ == "__main__":
    unittest.main()

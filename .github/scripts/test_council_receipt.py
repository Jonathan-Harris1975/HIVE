"""Negative evidence and authentication tests for the Council receipt."""

import copy
import unittest

from council_receipt import EvidenceError, verify

SHA = "a" * 40
POLICY = {
    "ci": "ci.yml",
    "deployment": "deployment.yml",
    "artifact": "deploy-",
    "dast": True,
    "dispatch_step": "Dispatch Council contract to Kilo",
}


class FakeGitHub:
    def __init__(self):
        self.head = SHA
        self.workflows = {
            "ci.yml": 1,
            "codeql.yml": 2,
            "security.yml": 3,
            "deployment.yml": 4,
            "dast.yml": 5,
            "council.yml": 6,
            "council-completion.yml": 7,
        }
        self.completion = {
            "workflow_id": 7,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": SHA,
            "created_at": "2026-10-04T20:00:00Z",
            "actor": {"login": "kilo-code-bot[bot]", "type": "Bot"},
            "triggering_actor": {"login": "kilo-code-bot[bot]", "type": "Bot"},
        }
        self.council = {
            "workflow_id": 6,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": SHA,
            "created_at": "2026-10-04T19:00:00Z",
        }
        self.runs = [
            {
                "id": i * 10,
                "workflow_id": i,
                "head_sha": SHA,
                "head_branch": "main",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "html_url": f"https://github.com/owner/repo/actions/runs/{i * 10}",
            }
            for i in range(1, 6)
        ]
        self.dispatched = True
        self.dast_jobs = [{"conclusion": "success"}]
        self.artifacts = [
            {
                "id": 1,
                "name": "deploy-" + SHA,
                "expired": False,
                "digest": "sha256:" + "b" * 64,
            }
        ]
        self.prs = []

    def request(self, path):
        if path == "":
            return {"default_branch": "main"}
        if path == "/commits/main":
            return {"sha": self.head}
        if path.startswith("/actions/workflows/"):
            return {"id": self.workflows[path.rsplit("/", 1)[1]]}
        if path == "/actions/runs/60":
            return self.council
        if path == "/actions/runs/70":
            return self.completion
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path == "/actions/runs/60/jobs":
            return [
                {
                    "steps": [
                        {
                            "name": POLICY["dispatch_step"],
                            "conclusion": "success" if self.dispatched else "failure",
                        }
                    ]
                }
            ]
        if path.startswith("/actions/runs?head_sha="):
            return self.runs
        if path == "/actions/runs/50/jobs":
            return self.dast_jobs
        if path == "/actions/runs/40/artifacts":
            return self.artifacts
        if path == "/pulls?state=open":
            return self.prs
        raise AssertionError(path)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()

    def verify(self, disposition="READY_FOR_COUNCIL_ACCEPTANCE", enabled=True):
        return verify(
            self.api, POLICY, SHA, 60, 70, "kilo-code-bot[bot]", disposition, enabled
        )

    def test_valid_authenticated_receipt_retains_independent_proofs(self):
        receipt = self.verify()
        self.assertEqual(receipt["target_sha"], SHA)
        self.assertEqual(len(receipt["proofs"]), 5)
        self.assertEqual(receipt["dast"], "verified")

    def test_declined_dispositions_cannot_certify(self):
        for disposition in ["HUMAN_HOLD", "NOT_READY", "success", ""]:
            with (
                self.subTest(disposition=disposition),
                self.assertRaises(EvidenceError),
            ):
                self.verify(disposition)

    def test_human_cannot_impersonate_kilo(self):
        self.api.completion["actor"]["type"] = "User"
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_different_bot_cannot_certify(self):
        self.api.completion["actor"]["login"] = "impostor[bot]"
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_human_rerun_cannot_certify(self):
        self.api.completion["triggering_actor"]["login"] = "owner"
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_stale_target_is_rejected(self):
        self.api.head = "c" * 40
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_wrong_council_workflow_is_rejected(self):
        self.api.council["workflow_id"] = 999
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_unconfirmed_dispatch_is_rejected(self):
        self.api.dispatched = False
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_new_failed_run_invalidates_older_success(self):
        newer = copy.deepcopy(self.api.runs[0])
        newer.update(id=100, conclusion="failure")
        self.api.runs.append(newer)
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_pull_request_evidence_cannot_certify_main(self):
        self.api.runs[0]["event"] = "pull_request"
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_enabled_skipped_dast_is_rejected(self):
        self.api.dast_jobs = [{"conclusion": "skipped"}]
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_disabled_dast_is_explicit_na(self):
        self.api.runs.pop()
        self.assertTrue(self.verify(enabled=False)["dast"].startswith("N/A"))

    def test_expired_or_unsigned_deployment_evidence_is_rejected(self):
        for patch in [{"expired": True}, {"digest": ""}, {"name": "deploy-old"}]:
            with self.subTest(patch=patch):
                self.api = FakeGitHub()
                self.api.artifacts[0].update(patch)
                with self.assertRaises(EvidenceError):
                    self.verify()

    def test_cancelled_source_council_is_rejected(self):
        self.api.council["conclusion"] = "cancelled"
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_default_head_change_during_verification_is_rejected(self):
        original = self.api.request
        calls = 0

        def changed_head(path):
            nonlocal calls
            if path == "/commits/main":
                calls += 1
                if calls == 2:
                    return {"sha": "c" * 40}
            return original(path)

        self.api.request = changed_head
        with self.assertRaises(EvidenceError):
            self.verify()

    def test_unresolved_repair_blocks_acceptance(self):
        self.api.prs = [{"labels": [{"name": "autonomy:repair"}]}]
        with self.assertRaises(EvidenceError):
            self.verify()


if __name__ == "__main__":
    unittest.main()

import copy
import json
import os
import unittest
from unittest.mock import patch

import merge_window as window
from council_receipt import EvidenceError

REPO = "owner/repo"
BASE = "a" * 40
OLD = "b" * 40
HEAD = "c" * 40
KILO = "kilo-code-bot[bot]"
POLICY = {
    "ci": "ci.yml",
    "ci_start": "Fri 22:30",
    "deployment": "deploy.yml",
    "dast": False,
}
CI = {
    "id": 10,
    "workflow_id": 1,
    "head_branch": "main",
    "head_sha": OLD,
    "event": "workflow_dispatch",
    "created_at": "2026-10-02T21:45:00Z",
}
BOT = {"login": "github-actions[bot]", "type": "Bot"}
PR = {
    "number": 3,
    "state": "open",
    "draft": False,
    "base": {"ref": "main"},
    "head": {"sha": HEAD, "repo": {"full_name": REPO}},
    "labels": [],
    "user": {"login": "person", "type": "User"},
    "html_url": f"https://github.com/{REPO}/pull/3",
}


class Fake:
    def __init__(self):
        self.data = {
            "": {"default_branch": "main"},
            "/commits/main": {"sha": BASE},
            "/actions/workflows/ci.yml": {"id": 1},
            "/actions/workflows/deploy.yml": {"id": 2},
            "/actions/workflows/security.yml": {"id": 3},
            "/actions/workflows/codeql.yml": {"id": 4},
            "/actions/workflows/council.yml": {"id": 5},
            "/actions/workflows/council-completion.yml/runs": [],
            "/actions/workflows/ci.yml/runs": [copy.deepcopy(CI)],
            f"/commits/{OLD}/statuses": [
                {
                    "id": 1,
                    "context": "Council evidence freeze 10",
                    "state": "success",
                    "creator": copy.deepcopy(BOT),
                    "description": "Envelope 2026-10-02T19:00:00+00:00",
                    "target_url": f"https://github.com/{REPO}/actions/runs/10",
                }
            ],
            f"/commits/{HEAD}/statuses": [],
            "/pulls?state=open": [copy.deepcopy(PR)],
            "/pulls/3": copy.deepcopy(PR),
            "/actions/runs/10": copy.deepcopy(CI),
            "/actions/runs/10/artifacts": [
                {
                    "name": window.freeze_name(CI),
                    "expired": False,
                    "digest": "sha256:" + "d" * 64,
                }
            ],
        }
        self.writes = []

    def request(self, path, data=None):
        if data is not None:
            self.writes.append((path, copy.deepcopy(data)))
            if path.startswith("/statuses/"):
                status_path = "/commits/" + path.split("/")[-1] + "/statuses"
                statuses = self.data.setdefault(status_path, [])
                statuses.append(
                    dict(data, id=len(statuses) + 100, creator=copy.deepcopy(BOT))
                )
            return {}
        return copy.deepcopy(self.data[path])

    def pages(self, path, key=None):
        return self.request(path)

    def completion(self, sha=OLD):
        completion = {
            "id": 30,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": sha,
            "created_at": "2026-10-04T20:10:00Z",
            "actor": {"login": KILO, "type": "Bot"},
            "triggering_actor": {"login": KILO, "type": "Bot"},
        }
        self.data["/actions/workflows/council-completion.yml/runs"] = [completion]
        self.data[f"/commits/{sha}/statuses"].append(
            {
                "id": 2,
                "context": "Repository Council acceptance",
                "state": "success",
                "creator": copy.deepcopy(BOT),
                "description": "Council 20; verified receipt 30",
                "target_url": f"https://github.com/{REPO}/actions/runs/30",
            }
        )
        self.data["/actions/runs/20"] = {
            "workflow_id": 5,
            "head_sha": sha,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-10-04T20:00:00Z",
        }
        self.data["/actions/runs/30/artifacts"] = [
            {
                "name": f"council-receipt-{sha}-20",
                "expired": False,
                "digest": "sha256:" + "d" * 64,
            }
        ]
        self.data[f"/compare/{sha}...{BASE}"] = {"behind_by": 0, "status": "ahead"}

    def failure(self):
        self.data["/actions/runs/40"] = {
            "id": 40,
            "workflow_id": 2,
            "head_sha": BASE,
            "head_branch": "main",
            "event": "workflow_dispatch",
            "created_at": "2026-10-03T10:00:00Z",
            "status": "completed",
            "conclusion": "failure",
        }
        self.data[f"/actions/runs?head_sha={BASE}"] = [
            copy.deepcopy(self.data["/actions/runs/40"])
        ]


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.api = Fake()

    def released(self):
        return window.released(self.api, POLICY, REPO, CI, "main", KILO, False)

    def test_dst_and_slot_boundaries(self):
        self.assertIsNotNone(window.window("2026-10-02T21:30:00Z", "Fri 22:30"))
        self.assertIsNone(window.window("2026-10-03T00:00:00Z", "Fri 22:30"))
        self.assertIsNotNone(window.window("2026-10-30T22:30:00Z", "Fri 22:30"))
        self.assertIsNone(window.window("2026-10-30T21:30:00Z", "Fri 22:30"))

    def test_repair_head_change_keeps_previous_ci_freeze(self):
        self.assertEqual(window.anchor(self.api, POLICY, REPO, "main")["head_sha"], OLD)
        window.reconcile(self.api, POLICY, REPO, KILO, False)
        self.assertEqual(self.api.writes[-1][1]["state"], "failure")

    def test_spoofed_ci_marker_ignored(self):
        self.api.data["/actions/runs/10/artifacts"] = []
        self.assertIsNone(window.anchor(self.api, POLICY, REPO, "main"))

    def test_pr_run_cannot_establish_default_freeze(self):
        self.api.data["/actions/workflows/ci.yml/runs"][0]["event"] = "pull_request"
        self.assertIsNone(window.anchor(self.api, POLICY, REPO, "main"))

    def test_completed_receipt_releases_and_survives_later_ordinary_merge(self):
        self.api.completion()
        self.assertTrue(self.released())

    def test_old_council_does_not_release_next_cycle(self):
        self.api.completion()
        self.api.data["/actions/runs/20"]["created_at"] = "2026-09-27T20:00:00Z"
        self.assertFalse(self.released())

    def test_incomplete_publisher_cannot_release(self):
        self.api.completion()
        self.api.data["/actions/workflows/council-completion.yml/runs"][0]["status"] = (
            "in_progress"
        )
        self.assertFalse(self.released())

    def test_human_rerun_cannot_release(self):
        self.api.completion()
        self.api.data["/actions/workflows/council-completion.yml/runs"][0][
            "triggering_actor"
        ] = {"login": "person", "type": "User"}
        self.assertFalse(self.released())

    def test_expired_receipt_cannot_release(self):
        self.api.completion()
        self.api.data["/actions/runs/30/artifacts"][0]["expired"] = True
        self.assertFalse(self.released())

    def test_divergent_history_cannot_release(self):
        self.api.completion()
        self.api.data[f"/compare/{OLD}...{BASE}"]["behind_by"] = 1
        self.assertFalse(self.released())

    def test_current_receipt_reverifies_all_live_evidence(self):
        self.api.data[f"/commits/{BASE}/statuses"] = []
        self.api.completion(BASE)
        with patch.object(
            window, "accepted_receipt", side_effect=EvidenceError("new DAST failure")
        ) as verify:
            with self.assertRaises(EvidenceError):
                self.released()
            verify.assert_called_once()

    def test_evidence_error_revokes_previous_successes_first(self):
        with (
            patch.object(
                window, "anchor", side_effect=EvidenceError("pagination limit")
            ),
            self.assertRaises(EvidenceError),
        ):
            window.reconcile(self.api, POLICY, REPO, KILO, False)
        self.assertEqual(self.api.writes[0][1]["state"], "pending")

    def test_changed_pr_head_is_rechecked_before_publish(self):
        self.api.data["/pulls/3"]["head"]["sha"] = "e" * 40
        window.publish_pr(self.api, POLICY, REPO, PR, CI, BASE, "main", KILO, False)
        self.assertEqual(self.api.writes[-1][0], "/statuses/" + "e" * 40)
        self.assertEqual(self.api.writes[-1][1]["state"], "failure")

    def test_human_hold_wins_after_council(self):
        self.api.data["/pulls/3"]["labels"] = [{"name": "autonomy:human-hold"}]
        window.publish_pr(self.api, POLICY, REPO, PR, CI, BASE, "main", KILO, True)
        self.assertEqual(self.api.writes[-1][1]["state"], "failure")

    def test_missing_repair_authorization_does_not_exempt_kilo(self):
        pr = copy.deepcopy(PR)
        pr["user"] = {"login": KILO, "type": "Bot"}
        pr["labels"] = [{"name": "autonomy:kilo-implementation"}]
        self.assertFalse(
            window.repair_allowed(self.api, POLICY, REPO, pr, CI, BASE, "main", KILO)
        )

    def test_old_and_superseded_failures_cannot_authorize(self):
        self.api.failure()
        self.api.data["/actions/runs/40"]["created_at"] = "2026-09-26T10:00:00Z"
        with self.assertRaises(EvidenceError):
            window.failure_current(
                self.api, POLICY, 40, BASE, "2026-10-02T19:00:00+00:00", "main"
            )
        self.api.failure()
        newer = copy.deepcopy(self.api.data["/actions/runs/40"])
        newer.update(id=41, conclusion="success")
        self.api.data[f"/actions/runs?head_sha={BASE}"].append(newer)
        with self.assertRaises(EvidenceError):
            window.failure_current(
                self.api, POLICY, 40, BASE, "2026-10-02T19:00:00+00:00", "main"
            )

    def test_failed_run_from_different_default_sha_cannot_authorize(self):
        self.api.failure()
        self.api.data["/actions/runs/40"]["head_sha"] = OLD
        with self.assertRaises(EvidenceError):
            window.failure_current(
                self.api, POLICY, 40, BASE, "2026-10-02T19:00:00+00:00", "main"
            )

    def test_recording_pass_blocks_prs_before_returning(self):
        self.api.data["/commits/main"]["sha"] = OLD
        with patch.object(window.Path, "write_text"):
            window.mark_pass(self.api, POLICY, REPO, 10, KILO, False)
        self.assertEqual(self.api.writes[0][1]["context"], "Council evidence freeze 10")
        self.assertEqual(self.api.writes[-1][1]["state"], "failure")

    def test_stale_ci_cannot_record_pass(self):
        with self.assertRaises(EvidenceError):
            window.mark_pass(self.api, POLICY, REPO, 10, KILO, False)
        self.assertEqual(self.api.writes, [])

    def repair_fixture(self):
        self.api.failure()
        pr = copy.deepcopy(PR)
        pr["user"] = {"login": KILO, "type": "Bot"}
        pr["labels"] = [{"name": "autonomy:kilo-implementation"}]
        self.api.data[f"/commits/{HEAD}/statuses"] = [
            {
                "id": 4,
                "state": "success",
                "context": window.AUTHORIZATION,
                "creator": copy.deepcopy(BOT),
                "description": f"Repair 40; CI 10/1; base {BASE}",
                "target_url": f"https://github.com/{REPO}/actions/runs/50",
            }
        ]
        self.api.data["/actions/workflows/trusted-automation.yml"] = {"id": 6}
        self.api.data["/actions/runs/50"] = {
            "id": 50,
            "workflow_id": 6,
            "head_branch": "main",
            "head_sha": BASE,
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
        }
        self.api.data["/actions/runs/50/artifacts"] = [
            {
                "name": f"council-repair-{HEAD}-40-10-1-{BASE}",
                "expired": False,
                "digest": "sha256:" + "d" * 64,
            }
        ]
        return pr

    def test_completed_authenticated_current_repair_is_exempt(self):
        pr = self.repair_fixture()
        self.assertTrue(
            window.repair_allowed(self.api, POLICY, REPO, pr, CI, BASE, "main", KILO)
        )

    def test_forged_status_without_producer_artifact_is_not_exempt(self):
        pr = self.repair_fixture()
        self.api.data["/actions/runs/50/artifacts"] = []
        self.assertFalse(
            window.repair_allowed(self.api, POLICY, REPO, pr, CI, BASE, "main", KILO)
        )

    def test_incomplete_authorizer_is_not_exempt(self):
        pr = self.repair_fixture()
        self.api.data["/actions/runs/50"]["status"] = "in_progress"
        self.assertFalse(
            window.repair_allowed(self.api, POLICY, REPO, pr, CI, BASE, "main", KILO)
        )

    def test_pr_code_cannot_authorize_repair(self):
        pr = self.repair_fixture()
        self.api.data["/actions/runs/50"]["event"] = "pull_request_review"
        self.assertFalse(
            window.repair_allowed(self.api, POLICY, REPO, pr, CI, BASE, "main", KILO)
        )

    def test_old_attempt_authorization_is_not_exempt(self):
        pr = self.repair_fixture()
        ci = dict(CI, run_attempt=2)
        self.assertFalse(
            window.repair_allowed(self.api, POLICY, REPO, pr, ci, BASE, "main", KILO)
        )

    def test_ci_rerun_requires_new_council_receipt(self):
        self.api.completion()
        ci = dict(CI, run_started_at="2026-10-04T21:00:00Z", run_attempt=2)
        self.assertFalse(
            window.released(self.api, POLICY, REPO, ci, "main", KILO, False)
        )

    def test_missing_freeze_artifact_on_successful_ci_holds(self):
        self.api.data["/actions/workflows/ci.yml/runs"][0].update(
            status="completed", conclusion="success"
        )
        self.api.data["/actions/runs/10/artifacts"] = []
        with self.assertRaises(EvidenceError):
            window.reconcile(self.api, POLICY, REPO, KILO, False)
        self.assertEqual(self.api.writes[0][1]["state"], "pending")

    def check_admission(self, frozen, closed=False):
        os.environ.setdefault("GH_TOKEN", "unit-test")
        os.environ.setdefault("GITHUB_REPOSITORY", REPO)
        import trusted_automation as admission

        pr = copy.deepcopy(PR)
        pr["user"]["login"] = "renovate[bot]"
        self.api.data["/pulls/3"] = pr
        with (
            patch.object(
                admission,
                "admission_kind"
                if hasattr(admission, "admission_kind")
                else "automation_kind",
                return_value="renovate",
            ),
            patch.object(admission, "is_same_repo", return_value=True),
            patch.object(admission, "renovate_auto_eligible", return_value=True),
            patch.object(admission, "emit_admission_status"),
            patch.object(admission, "revoke_admission"),
            patch.object(
                admission, "all_required_checks_green", return_value=(True, "green")
            ),
            patch.object(admission, "current_head_unchanged", return_value=pr),
            patch.object(admission, "approve_pr") as approve,
            patch.object(admission, "admit_to_mergify") as admit,
            patch.object(window, "GitHub", return_value=self.api),
            patch.object(window.Path, "read_text", return_value=json.dumps(POLICY)),
            patch.object(window, "anchor", return_value=CI if frozen else None),
            patch.object(window, "released", return_value=closed),
        ):
            admission.reconcile_pr(pr)
        return approve.call_count, admit.call_count

    def test_green_renovate_still_cannot_be_approved_or_admitted_when_frozen(self):
        self.assertEqual(self.check_admission(True), (0, 0))

    def test_green_renovate_can_be_admitted_before_freeze(self):
        self.assertEqual(self.check_admission(False), (1, 1))

    def test_green_renovate_can_be_admitted_after_certification(self):
        self.assertEqual(self.check_admission(True, closed=True), (1, 1))


if __name__ == "__main__":
    unittest.main()

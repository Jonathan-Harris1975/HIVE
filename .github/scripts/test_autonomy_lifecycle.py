"""Regression checks for repair retirement; all GitHub writes are mocked."""
import copy
import inspect
import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("GH_TOKEN", "unit-test")
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")

import branch_pr_automation as branch_controller  # noqa: E402
import trusted_automation as automation  # noqa: E402


class RepairRetirementTests(unittest.TestCase):
    def setUp(self):
        self.current = "a" * 40
        self.pr = {
            "number": 1,
            "state": "open",
            "title": "[autonomy] Repair CI failure (123)",
            "user": {"login": "repair[bot]"},
            "head": {"ref": "autonomy/repair-123", "repo": {"full_name": "owner/repo"}},
            "base": {"ref": "main"},
            "labels": [{"name": "autonomy:repair"}],
            "body": f"Failed commit: `{self.current}`",
        }
        for name, value in {"REPO": "owner/repo", "REPAIR_APP_LOGIN": "repair[bot]", "DEFAULT_BRANCH": "main"}.items():
            self.enterContext(patch.object(automation, name, value))
        self.enterContext(patch.object(automation, "get", return_value={"commit": {"sha": self.current}}))
        self.write = self.enterContext(patch.object(automation, "request"))
        self.labels = self.enterContext(patch.object(automation, "add_labels"))
        self.delete = self.enterContext(patch.object(automation, "delete"))
        self.enterContext(patch.object(automation, "log"))

    def retire(self):
        automation.reconcile_stale_carriers([copy.deepcopy(self.pr)])

    def test_current_unresolved_carrier_remains_open(self):
        self.retire()
        self.write.assert_not_called()

    def test_current_human_hold_remains_open(self):
        self.pr["labels"].append({"name": "autonomy:human-hold"})
        self.retire()
        self.write.assert_not_called()
        self.delete.assert_not_called()

    def test_obsolete_carrier_closes_even_at_current_sha(self):
        self.pr["labels"].append({"name": "autonomy:obsolete"})
        self.retire()
        self.write.assert_called_once_with("PATCH", "/repos/owner/repo/pulls/1", {"state": "closed"})
        self.delete.assert_called_once_with("/repos/owner/repo/issues/1/labels/autonomy%3Arepair", expected=(200, 204))

    def test_retirement_retries_after_active_label_was_removed(self):
        self.pr["labels"] = [{"name": "autonomy:superseded"}]
        self.retire()
        self.write.assert_called_once()

    def test_stale_carrier_is_labelled_and_closed(self):
        self.pr["body"] = f"Failed commit: `{'b' * 40}`"
        self.retire()
        self.labels.assert_called_once_with(1, ["autonomy:obsolete"])
        self.write.assert_called_once()

    def test_impostor_and_implementation_are_never_retired(self):
        self.pr["labels"].append({"name": "autonomy:obsolete"})
        self.pr["user"]["login"] = "another-user"
        self.retire()
        self.pr["user"]["login"] = "repair[bot]"
        self.pr["head"]["ref"] = "implementation/fix"
        self.retire()
        self.write.assert_not_called()

    def test_fork_carrier_is_never_retired(self):
        self.pr["labels"].append({"name": "autonomy:obsolete"})
        self.pr["head"]["repo"]["full_name"] = "fork/repo"
        self.retire()
        self.write.assert_not_called()

    def test_failed_close_keeps_active_labels_for_retry(self):
        self.pr["labels"].append({"name": "autonomy:obsolete"})
        self.write.side_effect = RuntimeError("simulated unavailable API")
        with self.assertRaises(RuntimeError):
            self.retire()
        self.delete.assert_not_called()


class ManagedBranchOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.sha = "c" * 40
        self.pr = {
            "number": 22,
            "state": "open",
            "draft": False,
            "title": "Implement requested change",
            "user": {"login": "repair[bot]"},
            "head": {
                "ref": "codex/requested-change",
                "sha": self.sha,
                "repo": {"full_name": "owner/repo"},
            },
            "base": {"ref": "main"},
            "labels": [{"name": "automation:branch-pr"}],
            "body": "",
        }
        for name, value in {
            "REPO": "owner/repo",
            "REPAIR_APP_LOGIN": "repair[bot]",
            "DEFAULT_BRANCH": "main",
        }.items():
            self.enterContext(patch.object(automation, name, value))
        self.enterContext(patch.object(automation, "log"))

    def test_branch_controller_has_no_native_merge_authority(self):
        source = inspect.getsource(branch_controller)
        self.assertNotIn("enablePullRequestAutoMerge", source)
        self.assertNotIn("disablePullRequestAutoMerge", source)
        self.assertNotIn("enable_native_auto_merge", source)
        self.assertNotIn('gh", "pr", "merge', source)

    def test_managed_branch_pr_is_admitted_to_mergify_after_green_checks(self):
        admit = self.enterContext(patch.object(automation, "admit_to_mergify"))
        approve = self.enterContext(patch.object(automation, "approve_pr"))
        self.enterContext(
            patch.object(automation, "pr_files", return_value=["backend/app/example.py"])
        )
        self.enterContext(
            patch.object(
                automation,
                "all_required_checks_green",
                return_value=(True, "green"),
            )
        )
        self.enterContext(
            patch.object(automation, "current_head_unchanged", return_value=self.pr)
        )

        automation.reconcile_pr(copy.deepcopy(self.pr))

        admit.assert_called_once_with(22)
        approve.assert_not_called()

    def test_managed_branch_pr_touching_protected_controls_gets_human_hold(self):
        hold = self.enterContext(patch.object(automation, "place_human_hold"))
        admit = self.enterContext(patch.object(automation, "admit_to_mergify"))
        self.enterContext(
            patch.object(
                automation,
                "pr_files",
                return_value=[".github/workflows/security.yml"],
            )
        )

        automation.reconcile_pr(copy.deepcopy(self.pr))

        hold.assert_called_once()
        admit.assert_not_called()


class KiloPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        cls.policy = json.loads((root / "kilo.jsonc").read_text(encoding="utf-8"))

    def test_kilo_is_non_interactive_but_cannot_self_amend_governance(self):
        permission = self.policy["permission"]
        self.assertEqual(permission["question"], "deny")
        self.assertEqual(permission["external_directory"], "deny")
        for tool in ("edit", "write", "apply_patch"):
            self.assertEqual(permission[tool]["*"], "allow")
            for path in ("kilo.jsonc", ".github/workflows/*", ".github/scripts/*", ".mergify.yml", "renovate.json"):
                self.assertEqual(permission[tool][path], "deny")

    def test_kilo_cannot_force_push_merge_or_deploy(self):
        bash = self.policy["permission"]["bash"]
        self.assertEqual(bash["*"], "deny")
        for command in (
            "git push +*",
            "git push --force",
            "git push -f",
            "git push --force-with-lease",
            "git push --mirror",
            "git push --delete",
            "git push *--force*",
            "git push * -f*",
            "bash -c *",
            "sh -c *",
            "gh pr merge *",
            "koyeb *",
        ):
            self.assertEqual(bash[command], "deny")
        for command in (
            "git push origin HEAD",
            "git push --set-upstream origin HEAD",
            "git push -u origin HEAD",
        ):
            self.assertEqual(bash[command], "allow")


class RenovateGovernanceTests(unittest.TestCase):
    def test_auto_eligible_renovate_governance_change_gets_human_hold(self):
        pr = {
            "number": 77,
            "state": "open",
            "draft": False,
            "title": "Update GitHub Actions dependency",
            "user": {"login": "renovate[bot]"},
            "head": {"ref": "renovate/actions", "sha": "d" * 40, "repo": {"full_name": "owner/repo"}},
            "base": {"ref": "main"},
            "labels": [{"name": "dependency:auto-eligible"}],
            "body": "This PR was generated by [Mend Renovate]\n<!--renovate-debug:test-->",
        }
        with (
            patch.object(automation, "REPO", "owner/repo"),
            patch.object(automation, "DEFAULT_BRANCH", "main"),
            patch.object(automation, "pr_files", return_value=[".github/workflows/ci.yml"]),
            patch.object(automation, "place_human_hold") as hold,
            patch.object(automation, "admit_to_mergify") as admit,
        ):
            automation.reconcile_pr(pr)
        hold.assert_called_once()
        admit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

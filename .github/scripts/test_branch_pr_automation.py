"""Behavioural safety tests for PR-only branch automation; no network access required."""
import copy
import importlib.util
import inspect
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("GH_TOKEN", "test-token")
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")

spec = importlib.util.spec_from_file_location(
    "branch_controller", Path(__file__).with_name("branch_pr_automation.py")
)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class BranchSafety(unittest.TestCase):
    def setUp(self):
        self.config = patch.multiple(
            m,
            REPO="owner/repo",
            DEFAULT_BRANCH="main",
            REPAIR_APP_LOGIN="repair[bot]",
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        self.pr = {
            "number": 7,
            "state": "open",
            "draft": False,
            "user": {"login": "repair[bot]"},
            "head": {
                "ref": "fix/example",
                "sha": "a" * 40,
                "repo": {"full_name": "owner/repo"},
            },
            "base": {"ref": "main"},
            "labels": [{"name": m.MANAGED_LABEL}],
        }

    def test_branch_namespaces(self):
        for name in ["fix/x", "feat/x", "chore/x", "ci/x", "work/x", "codex/x/y"]:
            self.assertTrue(m.allowed_branch(name), name)
        for name in [
            "main",
            "autonomy/repair-1",
            "renovate/x",
            "dependabot/x",
            "mergify/merge-queue/x",
            "tmp/x",
            "internal/x",
            "fix/",
            "fix/a..b",
            "fix/a//b",
            "fix/x;echo",
        ]:
            self.assertFalse(m.allowed_branch(name), name)

    def test_duplicate_matching_requires_same_repo_and_target(self):
        fork = copy.deepcopy(self.pr)
        fork["head"]["repo"]["full_name"] = "fork/repo"
        other = copy.deepcopy(self.pr)
        other["base"]["ref"] = "release"
        self.assertIsNone(m.exact_open_pr([fork, other], "fix/example"))
        self.assertEqual(m.exact_open_pr([fork, other, self.pr], "fix/example")["number"], 7)
        with self.assertRaises(RuntimeError):
            m.exact_open_pr([self.pr, copy.deepcopy(self.pr)], "fix/example")

    def test_existing_human_pr_is_reused_without_adoption(self):
        existing = copy.deepcopy(self.pr)
        existing["user"]["login"] = "owner"
        with (
            patch.object(m, "branch_signal_candidate", return_value=("fix/example", "a" * 40)),
            patch.object(m, "list_open_prs", return_value=[existing]),
            patch.object(m, "add_label") as label,
            patch.object(m, "post") as post,
        ):
            m.create_or_reuse_pr()
        label.assert_not_called()
        post.assert_not_called()

    def test_existing_repair_app_pr_recovers_managed_label(self):
        existing = copy.deepcopy(self.pr)
        existing["labels"] = []
        with (
            patch.object(m, "branch_signal_candidate", return_value=("fix/example", "a" * 40)),
            patch.object(m, "list_open_prs", return_value=[existing]),
            patch.object(m, "add_label") as label,
            patch.object(m, "post") as post,
        ):
            m.create_or_reuse_pr()
        label.assert_called_once_with(7)
        post.assert_not_called()

    def test_created_pr_is_labelled_but_never_merged(self):
        created = copy.deepcopy(self.pr)
        with (
            patch.object(m, "branch_signal_candidate", return_value=("codex/change", "a" * 40)),
            patch.object(m, "list_open_prs", return_value=[]),
            patch.object(m, "pr_metadata", return_value=("title", "body")),
            patch.object(m, "post", return_value=created) as post,
            patch.object(m, "add_label") as label,
        ):
            m.create_or_reuse_pr()
        self.assertIn("/pulls", post.call_args.args[0])
        label.assert_called_once_with(7)

    def test_recovery_does_not_run_on_push(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "push"}), patch.object(m, "get") as get:
            m.recover_branch_signals()
        get.assert_not_called()

    def test_scheduled_recovery_recreates_missing_ahead_branch_pr(self):
        branch_sha = "b" * 40

        def fake_get(path):
            if "/branches?" in path:
                return [{"name": "fix/lost-signal", "commit": {"sha": branch_sha}}]
            if "/pulls?state=open" in path:
                return []
            if "/pulls?" in path and "state=closed" in path:
                return []
            if "/compare/" in path:
                return {"ahead_by": 1}
            raise AssertionError(path)

        with (
            patch.dict(os.environ, {"GITHUB_EVENT_NAME": "schedule"}),
            patch.object(m, "get", side_effect=fake_get),
            patch.object(m, "create_or_reuse_pr") as create,
        ):
            m.recover_branch_signals()
        create.assert_called_once_with(("fix/lost-signal", branch_sha))

    def test_controller_contains_no_merge_authority(self):
        source = inspect.getsource(m)
        for forbidden in (
            "enablePullRequestAutoMerge",
            "disablePullRequestAutoMerge",
            "gh\", \"pr\", \"merge",
            "enable_native_auto_merge",
            "reconcile_managed_prs",
            "native_merge_policy",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

"""Regression coverage for admission feedback and competing repair PRs."""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")
os.environ.setdefault("GH_TOKEN", "test-token")
os.environ.setdefault("GITHUB_EVENT_NAME", "schedule")
os.environ.setdefault("DEFAULT_BRANCH", "main")
sys.path.insert(0, str(Path(__file__).parent))
import pr_repair_router as router
import pr_blocker_recovery as recovery


class RepairOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.source = {"number": 7, "html_url": "https://github.com/owner/repo/pull/7",
                       "user": {"login": "renovate[bot]"}, "head": {"sha": "a" * 40}}
        self.child = {"number": 8, "body": "Repairs " + self.source["html_url"],
                      "user": {"login": "kilo-code-bot[bot]"},
                      "head": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
                      "base": {"ref": "main"}}
        p = patch.multiple(router, REPO="owner/repo", DEFAULT="main", KILO_IMPLEMENTER="kilo-code-bot[bot]")
        p.start()
        self.addCleanup(p.stop)

    def test_verified_child_suppresses_another_kind_without_webhook(self):
        with patch.object(router, "all_pages", side_effect=[[], [self.child]]), \
             patch.object(router, "api", return_value={"behind_by": 0, "status": "ahead"}), \
             patch.object(router.urllib.request, "urlopen") as send:
            self.assertEqual(router.dispatch(self.source, "check", ["build failure"]), "implementation-active")
        send.assert_not_called()

    def test_untrusted_fork_wrong_base_and_prefix_links_do_not_own_source(self):
        for mutate in [lambda p: p["user"].update(login="someone"),
                       lambda p: p["head"]["repo"].update(full_name="other/repo"),
                       lambda p: p["base"].update(ref="other"),
                       lambda p: p.update(body=self.source["html_url"] + "0")]:
            child = copy.deepcopy(self.child)
            mutate(child)
            with self.subTest(child=child), patch.object(router, "all_pages", return_value=[child]), \
                 patch.object(router, "api") as compare:
                self.assertIsNone(router.active_implementation(self.source))
                compare.assert_not_called()

    def test_stale_ancestry_does_not_suppress_new_source(self):
        with patch.object(router, "all_pages", return_value=[self.child]), \
             patch.object(router, "api", return_value={"behind_by": 1, "status": "diverged"}):
            self.assertIsNone(router.active_implementation(self.source))

    def test_kilo_implementation_can_still_repair_itself(self):
        self.source["user"]["login"] = "kilo-code-bot[bot]"
        with patch.object(router, "all_pages") as pages:
            self.assertIsNone(router.active_implementation(self.source))
        pages.assert_not_called()

    def test_policy_failure_does_not_hide_real_build_failure(self):
        def api(method, path, payload=None):
            if "check-runs" in path:
                return {"check_runs": [
                    {"head_sha": "a" * 40, "status": "completed", "conclusion": "failure",
                     "name": "Trusted autonomy admission", "app": {"slug": "github-actions"}},
                    {"head_sha": "a" * 40, "status": "completed", "conclusion": "failure",
                     "name": "Build", "app": {"slug": "github-actions"}}]}
            return {"statuses": [{"state": "failure", "context": "Trusted autonomy admission",
                                  "creator": {"login": "github-actions[bot]"}}]}
        with patch.object(router, "api", side_effect=api):
            findings = recovery.current_head_failure_findings(self.source)
        self.assertEqual(len(findings), 1)
        self.assertIn("Build", findings[0])
        self.assertNotIn("admission", findings[0])

import unittest
from unittest.mock import patch

import recover_main_ci as recovery


class RecoverMainCITests(unittest.TestCase):
    def test_only_missing_exact_commit_is_dispatched(self):
        sha = "a" * 40
        with (patch.object(recovery, "REPO", "owner/repo"),
              patch.object(recovery, "TOKEN", "token"),
              patch.object(recovery, "CI_NAME", "CI"),
              patch.object(recovery, "api", side_effect=[
                  {"object": {"sha": sha}},
                  {"total_count": 0, "workflow_runs": []},
                  {"total_count": 0, "workflow_runs": []},
                  None,
              ]) as api):
            self.assertTrue(recovery.recover())
            api.assert_any_call("POST", "/repos/owner/repo/actions/workflows/ci.yml/dispatches", {"ref": "main"})

    def test_failed_ci_remains_evidence_without_dispatch(self):
        with (patch.object(recovery, "REPO", "owner/repo"),
              patch.object(recovery, "TOKEN", "token"),
              patch.object(recovery, "CI_NAME", "CI"),
              patch.object(recovery, "api", side_effect=[
                  {"object": {"sha": "b" * 40}},
                  {"total_count": 1, "workflow_runs": [{"name": "CI", "conclusion": "failure"}]},
              ]) as api):
            self.assertFalse(recovery.recover())
            self.assertEqual(api.call_count, 2)

    def test_partial_listing_never_dispatches(self):
        with (patch.object(recovery, "REPO", "owner/repo"),
              patch.object(recovery, "TOKEN", "token"),
              patch.object(recovery, "CI_NAME", "CI"),
              patch.object(recovery, "api", side_effect=[
                  {"object": {"sha": "c" * 40}},
                  {"total_count": 101, "workflow_runs": []},
              ]) as api):
            self.assertFalse(recovery.recover())
            self.assertEqual(api.call_count, 2)


if __name__ == "__main__":
    unittest.main()

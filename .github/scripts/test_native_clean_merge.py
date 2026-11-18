import unittest
from unittest.mock import patch

import native_auto_merge as native


class CleanMergeTests(unittest.TestCase):
    def test_clean_pr_uses_protected_exact_head_merge(self):
        sha = "a" * 40
        with (patch.object(native, "REPO", "owner/repo"),
              patch.object(native, "merge_method", return_value="SQUASH"),
              patch.object(native, "graphql", side_effect=RuntimeError("Pull request is in clean status")),
              patch.object(native, "request", return_value={"merged": True}) as request):
            self.assertTrue(native.enable("node", 7, sha))
            request.assert_called_once_with(
                "PUT", "/repos/owner/repo/pulls/7/merge",
                {"sha": sha, "merge_method": "squash"},
            )

    def test_declined_protected_merge_fails_closed(self):
        with (patch.object(native, "REPO", "owner/repo"),
              patch.object(native, "merge_method", return_value="SQUASH"),
              patch.object(native, "graphql", side_effect=RuntimeError("Pull request is in clean status")),
              patch.object(native, "request", return_value={"merged": False})):
            with self.assertRaises(RuntimeError):
                native.enable("node", 7, "a" * 40)


if __name__ == "__main__":
    unittest.main()

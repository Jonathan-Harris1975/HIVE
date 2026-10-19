"""Tests for explicit, authorised @kilocode-bot fix it routing."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GITHUB_REPOSITORY", "owner/repository")
os.environ.setdefault("GH_TOKEN", "test-token")
os.environ.setdefault("GITHUB_EVENT_NAME", "issue_comment")
os.environ.setdefault("DEFAULT_BRANCH", "main")
sys.path.insert(0, str(Path(__file__).parent))
import pr_repair_router as router


class ExplicitKiloFixCommandTests(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "number": 7,
            "html_url": "https://github.com/owner/repository/pull/7",
            "state": "open",
            "draft": False,
            "labels": [],
            "user": {"login": "owner"},
            "head": {"sha": "a" * 40, "ref": "feature", "repo": {"full_name": "owner/repository"}},
            "base": {"ref": "main"},
        }

    def event(self, body="@kilocode-bot fix it", association="OWNER", comment_id=912):
        return {
            "issue": {"number": 7, "pull_request": {"url": self.pr["html_url"]}},
            "comment": {
                "id": comment_id,
                "body": body,
                "html_url": f"{self.pr['html_url']}#issuecomment-{comment_id}",
                "user": {"login": "alice"},
                "author_association": association,
            },
        }

    def test_exact_owner_command_routes_current_pr(self):
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(self.event())
        self.assertIsNotNone(result)
        pr, kind, findings = result
        self.assertEqual(pr["number"], 7)
        self.assertEqual(kind, "fix-command")
        self.assertIn("explicitly requested", findings[0])
        self.assertIn("#issuecomment-912", findings[0])
        self.assertIn("a" * 40, findings[0])

    def test_repeated_commands_for_same_head_share_dedupe_key(self):
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            first = router.extract(self.event(comment_id=912))
            second = router.extract(self.event(comment_id=913))
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[1], "fix-command")

    def test_empty_kilo_review_evidence_is_ignored(self):
        event = self.event(body="No findings")
        event["comment"]["user"] = {"login": "kilo-code-bot[bot]"}
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(event)
        self.assertIsNone(result)

    def test_non_actionable_kilo_comment_stays_ignored(self):
        event = self.event(body="Reply with @kilocode-bot fix it")
        event["comment"]["user"] = {"login": "kilo-code-bot[bot]"}
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(event)
        self.assertIsNone(result)

    def test_exact_command_from_untrusted_actor_is_ignored(self):
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(self.event(association="NONE"))
        self.assertIsNone(result)

    def test_command_with_extra_text_is_not_treated_as_authorised_shortcut(self):
        with patch.object(router, "EVENT", "issue_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(self.event(body="@kilocode-bot fix it and also do something else"))
        self.assertIsNone(result)

    def test_exact_command_also_routes_from_inline_review_reply(self):
        event = {
            "pull_request": {"number": 7},
            "comment": {
                "id": 913,
                "body": "@kilocode-bot fix it",
                "html_url": f"{self.pr['html_url']}#discussion_r913",
                "user": {"login": "alice"},
                "author_association": "COLLABORATOR",
                "path": "src/example.py",
            },
        }
        with patch.object(router, "EVENT", "pull_request_review_comment"), patch.object(
            router, "pr_details", return_value=self.pr
        ):
            result = router.extract(event)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "fix-command")
        self.assertIn("src/example.py", result[2][0])


if __name__ == "__main__":
    unittest.main()

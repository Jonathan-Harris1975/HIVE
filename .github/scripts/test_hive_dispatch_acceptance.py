import importlib.util, os, sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repository")
os.environ.setdefault("GH_TOKEN", "test-token")
os.environ.setdefault("DEFAULT_BRANCH", "main")
os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
spec = importlib.util.spec_from_file_location("pr_repair_router_under_test", ROOT / "pr_repair_router.py")
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)
SHA = "a" * 40
PR = {"number": 17, "head": {"sha": SHA}, "html_url": "https://github.com/owner/repository/pull/17"}
class HiveDispatchTests(unittest.TestCase):
    def event(self, **overrides):
        inputs = {
            "pr_number": "17",
            "review_id": "review-" + "b" * 24,
            "source_head_sha": SHA,
            "repair_prompt": (
                "Repair the verified failure at the exact source head "
                "and retain all required controls."
            ),
        }
        inputs.update(overrides)
        return {"inputs": inputs}
    def test_valid_review_dispatch_requires_live_exact_head_and_bounded_prompt(self):
        with patch.object(router, "pr_details", return_value=PR):
            result = router.extract(self.event())
        self.assertEqual(result[0]["number"], 17)
        self.assertEqual(result[1], "review")
        self.assertIn("review-" + "b" * 24, result[2][0])
        self.assertIn("Repair the verified failure", result[2][1])
    def test_moved_head_is_rejected(self):
        with patch.object(router, "pr_details", return_value=PR):
            self.assertIsNone(router.extract(self.event(source_head_sha="c" * 40)))
    def test_malformed_review_id_and_unbounded_prompt_are_rejected(self):
        with patch.object(router, "pr_details", return_value=PR):
            self.assertIsNone(router.extract(self.event(review_id="untrusted")))
            self.assertIsNone(router.extract(self.event(repair_prompt="x" * 3501)))
    def test_closed_or_ineligible_pr_is_rejected(self):
        with patch.object(router, "pr_details", return_value=None):
            self.assertIsNone(router.extract(self.event()))
if __name__ == "__main__": unittest.main()

"""Offline regression checks for the weekend orchestrator; the GitHub API is faked."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("GH_TOKEN", "unit-test")
os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")

import weekend_orchestrator as wo  # noqa: E402

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc


def london(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=LONDON)


class TimeTests(unittest.TestCase):
    def test_ci_boundary_is_saturday_1100_london(self):
        self.assertEqual(wo.ci_window_close_for(london(2026, 10, 3, 12, 0)), london(2026, 10, 3, 11, 0))
        self.assertEqual(wo.ci_window_close_for(london(2026, 10, 10, 12, 0)), london(2026, 10, 10, 11, 0))

    def test_council_not_before_sunday_1600(self):
        self.assertEqual(wo.council_not_before(london(2026, 10, 3, 12, 0)), london(2026, 10, 4, 16, 0))

    def test_guard_accepts_only_first_hour_of_ci_slot(self):
        # BST: Saturday 10:00 UTC is 11:00 London (inside); 11:00 UTC is 12:00 London (outside).
        self.assertTrue(wo.in_launch_window(datetime(2026, 10, 3, 10, 0, tzinfo=UTC)))
        self.assertFalse(wo.in_launch_window(datetime(2026, 10, 3, 11, 0, tzinfo=UTC)))
        # GMT: Saturday 11:00 UTC is 11:00 London.
        self.assertTrue(wo.in_launch_window(datetime(2026, 11, 7, 11, 0, tzinfo=UTC)))

    def test_guard_rejects_other_days(self):
        self.assertFalse(wo.in_launch_window(datetime(2026, 10, 5, 10, 0, tzinfo=UTC)))


class DashboardTests(unittest.TestCase):
    def test_pending_section_count(self):
        body = ("## Open\n- [ ] update a\n## Pending Status Checks\n"
                "- [ ] <!-- b -->update b\n- [ ] <!-- c -->update c\n## Other\n- [ ] x\n")
        self.assertEqual(wo.count_pending_section(body), 2)

    def test_no_section(self):
        self.assertEqual(wo.count_pending_section("## Open\n- [ ] a\n"), 0)


def pr(number, labels=(), head="feature", login="human", body="", updated="2026-10-04T01:00:00Z"):
    return {"number": number, "labels": [{"name": x} for x in labels], "head": {"ref": head},
            "user": {"login": login}, "body": body, "updated_at": updated}


class FakeApi:
    def __init__(self):
        self.prs = []
        self.inflight = 0
        self.sha = "a" * 40
        self.posts = []
        self.runs = {}

    def get(self, path):
        if path.endswith(f"/commits/main"):
            return {"sha": self.sha}
        if "actions/runs?status=" in path:
            return {"total_count": self.inflight}
        if "/issues?state=open" in path:
            return []
        if "/runs?head_sha=" in path:
            workflow = path.split("/workflows/")[1].split("/")[0]
            return {"workflow_runs": self.runs.get(workflow, [])}
        raise AssertionError(path)

    def pages(self, path, key=None):
        return self.prs

    def call(self, method, path, payload=None, expected=None):
        self.posts.append((method, path, payload))


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def make(api, **kwargs):
    clock = Clock()
    cfg = wo.Config(repo="owner/repo", token="t", poll_seconds=60, **kwargs)
    return wo.Orchestrator(cfg, api=api, clock=clock.monotonic, sleep=clock.sleep,
                           now=lambda: datetime(2026, 10, 4, 1, 40, tzinfo=UTC)), clock


class SettleTests(unittest.TestCase):
    def test_settles_when_nothing_open(self):
        api = FakeApi()
        orch, _ = make(api)
        self.assertTrue(orch.settle())

    def test_human_prs_do_not_block(self):
        api = FakeApi()
        api.prs = [pr(5)]
        orch, _ = make(api)
        self.assertTrue(orch.settle())

    def test_admitted_pr_blocks_until_timeout(self):
        api = FakeApi()
        api.prs = [pr(7, labels=["autonomy:admitted"])]
        orch, _ = make(api, settle_minutes=5)
        self.assertFalse(orch.settle())
        self.assertEqual(orch.stages[-1]["outcome"], "timeout")

    def test_inflight_pr_runs_block(self):
        api = FakeApi()
        api.inflight = 2
        orch, _ = make(api, settle_minutes=3)
        self.assertFalse(orch.settle())

    def test_renovate_automerge_pr_and_legacy_queue_branch_block(self):
        api = FakeApi()
        api.prs = [pr(8, login="renovate[bot]", body="**Automerge**: Enabled."),
                   pr(9, head="mergify/merge-queue/abc")]
        orch, _ = make(api, settle_minutes=2)
        found = orch.automation_prs(api.prs)
        self.assertEqual({p["number"] for p in found}, {8, 9})

    def test_renovate_major_without_automerge_is_ignored(self):
        api = FakeApi()
        api.prs = [pr(10, login="renovate[bot]", body="**Automerge**: Disabled by config.")]
        orch, _ = make(api)
        self.assertEqual(orch.automation_prs(api.prs), [])

    def test_idle_admitted_pr_uses_trusted_reconciliation_without_comment(self):
        api = FakeApi()
        api.prs = [pr(7, labels=["autonomy:admitted"], updated="2026-10-04T00:00:00Z")]
        orch, _ = make(api, settle_minutes=3, nudge_minutes=1)
        orch.settle()
        dispatches = [p for p in api.posts if p[1].endswith("/workflows/trusted-automation.yml/dispatches")]
        comments = [p for p in api.posts if "/comments" in p[1]]
        self.assertEqual(len(dispatches), 2)
        self.assertEqual(comments, [])


class CouncilGateTests(unittest.TestCase):
    def test_blocked_by_unresolved_repair(self):
        api = FakeApi()
        api.prs = [pr(3, labels=["autonomy:repair"])]
        orch, _ = make(api)
        orch.now = lambda: datetime(2026, 10, 4, 15, 30, tzinfo=UTC)  # 16:30 London BST, inside Council slot
        self.assertFalse(orch.release_council(api.sha, "0"))
        self.assertEqual(orch.stages[-1]["outcome"], "blocked")

    def test_defers_if_head_moved(self):
        api = FakeApi()
        orch, _ = make(api)
        orch.now = lambda: datetime(2026, 10, 4, 15, 30, tzinfo=UTC)
        self.assertFalse(orch.release_council("b" * 40, "0"))
        self.assertEqual(orch.stages[-1]["outcome"], "deferred")

    def test_dry_run_never_dispatches(self):
        api = FakeApi()
        orch, _ = make(api, dry_run=True)
        orch.now = lambda: datetime(2026, 10, 4, 15, 30, tzinfo=UTC)
        self.assertTrue(orch.release_council(api.sha, "0"))
        self.assertFalse([p for p in api.posts if "council.yml" in p[1]])

    def test_waits_until_one_hour_after_window(self):
        api = FakeApi()
        orch, clock = make(api, dry_run=True)
        state = {"t": datetime(2026, 10, 4, 14, 40, tzinfo=UTC)}  # 15:40 London BST
        orch.now = lambda: state["t"]

        def sleep(seconds):
            state["t"] += timedelta(seconds=seconds)
        orch.sleep = sleep
        self.assertTrue(orch.release_council(api.sha, "0"))
        self.assertGreaterEqual(state["t"].astimezone(LONDON), london(2026, 10, 4, 16, 0))


class VerifyTests(unittest.TestCase):
    def test_missed_watcher_retries_ci_without_dispatching_credentialed_watcher(self):
        api = FakeApi()
        now = "2026-10-04T01:40:00+00:00"
        for workflow in ("ci.yml", "codeql.yml", "security.yml"):
            api.runs[workflow] = [{
                "id": 1, "created_at": now, "event": "workflow_dispatch",
                "status": "completed", "conclusion": "success",
            }]

        original_call = api.call
        def call(method, path, payload=None, expected=None):
            original_call(method, path, payload, expected)
            ci_dispatches = [post for post in api.posts
                             if post[1].endswith("/workflows/ci.yml/dispatches")]
            if len(ci_dispatches) == 2:
                api.runs[wo.DEPLOY_WORKFLOW_FILE] = [{
                    "id": 2, "created_at": now, "event": "workflow_run",
                    "status": "completed", "conclusion": "success",
                }]
        api.call = call

        orch, _ = make(api)
        self.assertEqual(orch.verify(api.sha), [])
        ci_dispatches = [post for post in api.posts
                         if post[1].endswith("/workflows/ci.yml/dispatches")]
        deployment_dispatches = [post for post in api.posts
                                 if post[1].endswith(f"/workflows/{wo.DEPLOY_WORKFLOW_FILE}/dispatches")]
        self.assertEqual(len(ci_dispatches), 2)
        self.assertEqual(deployment_dispatches, [])


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""Behavioural blocker-recovery tests; all network calls are mocked."""

from io import BytesIO, StringIO
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("GITHUB_REPOSITORY", "owner/repo")
os.environ.setdefault("GH_TOKEN", "test-token")
os.environ.setdefault("GITHUB_EVENT_NAME", "schedule")
os.environ.setdefault("DEFAULT_BRANCH", "main")
os.environ.setdefault("KILO_DEBUG_TRIGGER_URL", "https://hooks.kilosessions.ai/test")
sys.path.insert(0, str(Path(__file__).parent))
import pr_blocker_recovery as m


class Recovery(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "number": 7,
            "html_url": "https://github.com/owner/repo/pull/7",
            "state": "open",
            "draft": False,
            "labels": [],
            "user": {"login": "owner"},
            "head": {"sha": "a" * 40, "ref": "ci/test", "repo": {"full_name": "owner/repo"}},
            "base": {"ref": "main"},
            "mergeable": True,
            "mergeable_state": "clean",
        }
        self.thread = {
            "id": "PRRT_test",
            "isResolved": False,
            "isOutdated": True,
            "path": ".github/workflows/security.yml",
            "line": None,
            "comments": {
                "nodes": [
                    {
                        "body": "Fix the missing independent validation in the current security workflow.",
                        "author": {"login": "kilo-code-bot"},
                    }
                ]
            },
        }
        self.config = patch.multiple(
            m.router,
            REPO="owner/repo",
            DEFAULT="main",
            KILO_IMPLEMENTER="kilo-code-bot[bot]",
            REPAIR_APP_LOGIN="repair[bot]",
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        original_all_pages = m.router.all_pages
        self.comment_pages = patch.object(
            m.router,
            "all_pages",
            side_effect=lambda path: (
                []
                if path.endswith("/issues/7/comments")
                else original_all_pages(path)
            ),
        )
        self.comment_pages.start()
        self.addCleanup(self.comment_pages.stop)

    def receipt(self, author="repair[bot]", sha=None, base=None):
        return {
            "user": {"login": author},
            "body": "<!-- pr-blocker-resolution:"
            + json.dumps(
                {
                    "sha": sha or "a" * 40,
                    "base_sha": base or "b" * 40,
                    "threads": [
                        {
                            "id": "PRRT_test",
                            "evidence": "Implemented independent validation at workflow line 35; regression tests passed.",
                        }
                    ],
                }
            )
            + " -->",
        }

    def api(self, method, path, payload=None):
        if path.endswith("/commits/main"):
            return {"sha": "b" * 40}
        if path == "/graphql":
            return {"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}}
        raise AssertionError((method, path))

    def test_conflict_routes_even_without_failed_ci(self):
        self.pr.update(mergeable=False, mergeable_state="dirty")
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router, "dispatch", return_value="requested") as dispatch,
            patch.object(m, "review_threads") as threads,
        ):
            result = m.recover(7)
        self.assertEqual(result["state"], "conflict-requested")
        self.assertEqual(dispatch.call_args.args[1], "merge-conflict-" + "b" * 40)
        threads.assert_not_called()

    def test_behind_branch_routes_existing_source_update(self):
        self.pr["mergeable_state"] = "behind"
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router, "dispatch", return_value="requested") as dispatch,
        ):
            self.assertEqual(m.recover(7)["state"], "behind-requested")
        self.assertEqual(dispatch.call_args.args[1], "branch-behind-" + "b" * 40)

    def test_exhausted_attempts_are_visible_not_reported_as_requested(self):
        self.pr["mergeable"] = False
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router, "dispatch", return_value="attempt-limit"),
        ):
            self.assertEqual(m.recover(7)["state"], "conflict-attempt-limit")

    def test_unknown_mergeability_does_not_guess(self):
        self.pr["mergeable"] = None
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            self.assertEqual(m.recover(7)["state"], "mergeability-pending")
        dispatch.assert_not_called()

    def test_every_owner_hold_is_respected(self):
        self.assertIn("autonomy:human-hold", m.HOLD_LABELS)
        for label in m.HOLD_LABELS:
            self.pr["labels"] = [{"name": label}]
            with (
                patch.object(m.router, "pr_details", return_value=self.pr),
                patch.object(m.router, "api") as api,
            ):
                self.assertEqual(m.recover(7)["state"], "excluded")
                api.assert_not_called()

    def test_ineligible_pr_is_excluded(self):
        with patch.object(m.router, "pr_details", return_value=None):
            self.assertEqual(m.recover(7)["state"], "excluded")

    def test_receipt_rejects_untrusted_author_and_stale_tips(self):
        for receipt in [
            self.receipt(author="owner"),
            self.receipt(sha="c" * 40),
            self.receipt(base="c" * 40),
        ]:
            self.assertEqual(m.verified_receipts([receipt], "a" * 40, "b" * 40), {})
        self.assertIn("PRRT_test", m.verified_receipts([self.receipt()], "a" * 40, "b" * 40))
        self.assertEqual(
            m.verified_receipts([self.receipt(author="kilo-code-bot[bot]")], "a" * 40, "b" * 40), {}
        )

    def test_receipt_accepts_configured_app_identity(self):
        self.assertIn(
            "PRRT_test",
            m.verified_receipts([self.receipt(author="repair[bot]")], "a" * 40, "b" * 40),
        )

    def test_receipt_rejects_missing_implementation_evidence(self):
        receipt = self.receipt()
        receipt["body"] = receipt["body"].replace(
            "Implemented independent validation at workflow line 35; regression tests passed.",
            "fixed",
        )
        self.assertEqual(m.verified_receipts([receipt], "a" * 40, "b" * 40), {})

    def test_outdated_thread_is_routed_not_blindly_resolved(self):
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api) as api,
            patch.object(m, "review_threads", return_value=[self.thread]),
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            self.assertEqual(m.recover(7)["bot_threads_remaining"], 1)
        self.assertFalse(any(c.args[1] == "/graphql" for c in api.call_args_list))
        dispatch.assert_called_once()

    def test_verified_receipt_and_required_checks_resolve_thread(self):
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m, "review_threads", return_value=[self.thread]),
            patch.object(m.router, "all_pages", return_value=[self.receipt()]),
            patch.object(m, "required_checks_pass", return_value=True),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            self.assertEqual(m.recover(7)["resolved"], ["PRRT_test"])
        dispatch.assert_not_called()

    def test_failed_required_checks_preserve_review(self):
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api) as api,
            patch.object(m, "review_threads", return_value=[self.thread]),
            patch.object(m.router, "all_pages", return_value=[self.receipt()]),
            patch.object(m, "required_checks_pass", return_value=False),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            result = m.recover(7)
            self.assertEqual(result["resolved"], [])
            self.assertEqual(result["bot_threads_remaining"], 0)
        dispatch.assert_not_called()
        self.assertFalse(any(c.args[1] == "/graphql" for c in api.call_args_list))

    def test_head_movement_before_resolution_defers(self):
        changed = copy.deepcopy(self.pr)
        changed["head"]["sha"] = "c" * 40
        with (
            patch.object(m.router, "pr_details", side_effect=[self.pr, changed]),
            patch.object(m.router, "api", side_effect=self.api) as api,
            patch.object(m, "review_threads", return_value=[self.thread]),
            patch.object(m.router, "all_pages", return_value=[self.receipt()]),
            patch.object(m, "required_checks_pass", return_value=True),
        ):
            self.assertEqual(m.recover(7)["state"], "changed-during-recovery")
        self.assertFalse(any(c.args[1] == "/graphql" for c in api.call_args_list))

    def test_human_review_threads_are_never_resolved_or_routed(self):
        self.thread["comments"]["nodes"][0]["author"]["login"] = "human"
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api) as api,
            patch.object(m, "review_threads", return_value=[self.thread]),
            patch.object(m.router, "all_pages", return_value=[self.receipt()]),
            patch.object(m, "required_checks_pass", return_value=True),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            self.assertEqual(m.recover(7)["human_threads_remaining"], 1)
        dispatch.assert_not_called()
        self.assertFalse(any(c.args[1] == "/graphql" for c in api.call_args_list))

    def test_graphql_errors_are_not_a_clean_review(self):
        with patch.object(m.router, "api", return_value={"errors": [{}]}):
            with self.assertRaises(RuntimeError):
                m.review_threads(7)

    def test_no_native_required_checks_never_authorises_resolution(self):
        with patch.object(m.router, "api", return_value=[]):
            self.assertFalse(m.required_checks_pass(self.pr))

    def test_check_requires_success_from_expected_app(self):
        requirement = [
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [{"context": "security", "integration_id": 1}]
                },
            }
        ]
        for conclusion, app, expected in [
            ("success", 1, True),
            ("failure", 1, False),
            ("skipped", 1, False),
            ("success", 2, False),
        ]:
            checks = {
                "check_runs": [
                    {
                        "name": "security",
                        "app": {"id": app},
                        "status": "completed",
                        "conclusion": conclusion,
                    }
                ]
            }
            rulesets = [{"id": 1, "enforcement": "active", "target": "branch"}]
            detail = {
                "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}},
                "rules": requirement,
            }
            with patch.object(
                m.router, "api", side_effect=[rulesets, detail, checks, {"statuses": []}]
            ):
                self.assertEqual(m.required_checks_pass(self.pr), expected)

    def test_manual_pr_selection_and_invalid_number(self):
        self.assertEqual(m.candidate_numbers({"inputs": {"pr_number": "7"}}), [7])
        with self.assertRaises(ValueError):
            m.candidate_numbers({"inputs": {"pr_number": "7;bad"}})

    def test_untrusted_comment_does_not_start_recovery(self):
        event = {
            "issue": {"number": 7, "pull_request": {}},
            "comment": {"user": {"login": "outsider"}, "author_association": "NONE"},
        }
        event["issue"]["pull_request"] = {"url": "https://example.invalid/pr/7"}
        self.assertEqual(m.candidate_numbers(event), [])
        event["comment"]["author_association"] = "COLLABORATOR"
        self.assertEqual(m.candidate_numbers(event), [7])
        event["comment"] = {"user": {"login": "kilo-code-bot[bot]"}}
        self.assertEqual(m.candidate_numbers(event), [7])

    def test_required_check_on_second_rule_page_cannot_be_omitted(self):
        first = [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "green"}]},
            }
        ] + [{"type": "dummy"}] * 99
        second = [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "red"}]},
            }
        ]
        checks = {
            "check_runs": [
                {"name": name, "status": "completed", "conclusion": result}
                for name, result in [("green", "success"), ("red", "failure")]
            ]
        }
        with patch.object(
            m.router, "api", side_effect=[first, second, checks, {"statuses": []}]
        ) as api:
            self.assertFalse(m.required_checks_pass(self.pr))
        self.assertIn("page=2", api.call_args_list[1].args[1])

    def test_reviews_and_inline_comments_require_trusted_actor(self):
        for key in ["review", "comment"]:
            event = {
                "pull_request": {"number": 7},
                key: {"user": {"login": "outsider"}, "author_association": "NONE"},
            }
            self.assertEqual(m.candidate_numbers(event), [])
            event[key]["user"]["login"] = "repair[bot]"
            self.assertEqual(m.candidate_numbers(event), [7])

    def test_workflow_authentication_precedes_write_scoped_job(self):
        workflow = (Path(__file__).parents[1] / "workflows/pr-issue-repair.yml").read_text()
        actor_code = workflow.split("        python3 - <<'PYCODE'\n", 1)[1].split(
            "        PYCODE", 1
        )[0]
        actor_code = "\n".join(line[8:] for line in actor_code.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            for event in ["issue_comment", "pull_request_review", "pull_request_review_comment"]:
                for who, expected in [
                    ("outsider", "false"),
                    ("chatgpt-codex-connector", "false"),
                    ("repair[bot]", "true"),
                ]:
                    output.write_text("")
                    with patch.dict(
                        os.environ,
                        {
                            "EVENT_NAME": event,
                            "ACTOR_LOGIN": who,
                            "ACTOR_ASSOCIATION": "NONE",
                            "KILO_LOGIN": "",
                            "REPAIR_LOGIN": "repair[bot]",
                            "GITHUB_OUTPUT": str(output),
                        },
                    ):
                        exec(actor_code, {})
                    self.assertEqual(output.read_text(), "trusted=" + expected + "\n")
        self.assertIn("needs: authenticate_actor", workflow)
        self.assertIn("needs.authenticate_actor.outputs.trusted == 'true'", workflow)
        self.assertIn(
            "REPAIR_APP_LOGIN: ${{ needs.authenticate_actor.outputs.repair_app_login }}", workflow
        )

    def test_rule_page_limit_does_not_authorise_resolution(self):
        with patch.object(m.router, "api", return_value=[{"type": "dummy"}] * 100):
            with self.assertRaises(ValueError):
                m.required_checks_pass(self.pr)

    @patch.dict(os.environ, {"KILO_REPAIR_TRIGGER_URL": "https://example.invalid/repair"})
    def test_behind_recovery_uses_real_dispatcher_return_and_existing_branch(self):
        self.pr["mergeable_state"] = "behind"
        with (
            patch.dict(os.environ, {"KILO_REPAIR_TRIGGER_URL": "https://hooks.kilosessions.ai/test"}, clear=False),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(
                m.router,
                "api",
                side_effect=lambda method, path, payload=None: (
                    {"sha": "b" * 40} if method == "GET" else
                    {"id": 99} if method == "POST" else {}
                ),
            ),
            patch.object(m.router.urllib.request, "urlopen") as openurl,
        ):
            openurl.return_value.__enter__.return_value.status = 202
            self.assertEqual(m.recover(7)["state"], "behind-requested")
            task = json.loads(openurl.call_args.args[0].data)["task"]
            self.assertIn("existing source PR branch", task)
            self.assertNotIn("create one implementation PR", task)

    def test_one_pr_error_does_not_stop_others_and_fails_visibly(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text("{}")
            summary = Path(directory) / "summary.md"
            previous = Path.cwd()
            os.chdir(directory)
            try:
                with (
                    patch.dict(
                        os.environ,
                        {"GITHUB_EVENT_PATH": str(event), "GITHUB_STEP_SUMMARY": str(summary)},
                    ),
                    patch.object(m, "candidate_numbers", return_value=[7, 8]),
                    patch("builtins.print"),
                    patch.object(
                        m,
                        "recover",
                        side_effect=[
                            RuntimeError("hidden detail"),
                            {"pr": 8, "state": "no-conflict-or-review-blocker"},
                        ],
                    ),
                ):
                    with self.assertRaises(SystemExit):
                        m.main()
                report = json.loads(Path("pr-blocker-recovery.json").read_text())
                self.assertEqual(len(report["results"]), 2)
                self.assertNotIn("hidden detail", summary.read_text())
            finally:
                os.chdir(previous)

    def test_conflict_dispatch_requests_existing_branch_and_no_force_push(self):
        with (
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router.urllib.request, "urlopen") as openurl,
            patch.dict(os.environ, {"KILO_REPAIR_TRIGGER_URL": "https://example.invalid/repair"}),
        ):
            openurl.return_value.__enter__.return_value.status = 202

            # The acknowledgement write is mocked separately from base reads.
            def api(method, path, payload=None):
                if method == "GET":
                    return {"sha": "b" * 40}
                if method == "POST":
                    return {"id": 99}
                return {}

            with patch.object(m.router, "api", side_effect=api):
                m.router.dispatch(self.pr, "merge-conflict-" + "b" * 40, ["conflict"])
            task = json.loads(openurl.call_args.args[0].data)["task"]
            self.assertIn("existing source PR branch", task)
            self.assertIn("Never force-push", task)
            self.assertIn("Do not merge pull requests or deploy", task)

    def test_dispatch_head_race_makes_no_webhook_call(self):
        changed = copy.deepcopy(self.pr)
        changed["head"]["sha"] = "c" * 40
        with (
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "pr_details", return_value=changed),
            patch.object(m.router, "api", side_effect=self.api),
            patch.object(m.router.urllib.request, "urlopen") as openurl,
        ):
            m.router.dispatch(self.pr, "merge-conflict-" + "b" * 40, ["conflict"])
            openurl.assert_not_called()

    def test_human_hold_added_before_dispatch_prevents_kilo_request(self):
        held = copy.deepcopy(self.pr)
        held["labels"] = [{"name": "autonomy:human-hold"}]
        with (
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "pr_details", return_value=held),
            patch.object(m.router, "api", return_value={"sha": "b" * 40}) as api,
            patch.object(m.router.urllib.request, "urlopen") as openurl,
        ):
            result = m.router.dispatch(self.pr, "merge-conflict-" + "b" * 40, ["conflict"])
        self.assertEqual(result, "changed-before-dispatch")
        api.assert_called_once_with("GET", "/repos/owner/repo/commits/main")
        openurl.assert_not_called()

    def test_duplicate_exact_head_base_is_not_dispatched(self):
        marker = "<!-- kilo-auto-repair:" + "a" * 40 + ":merge-conflict-" + "b" * 40 + " -->"
        with (
            patch.object(
                m.router,
                "all_pages",
                return_value=[{"user": {"login": "github-actions[bot]"}, "body": marker}],
            ),
            patch.object(m.router.urllib.request, "urlopen") as openurl,
        ):
            m.router.dispatch(self.pr, "merge-conflict-" + "b" * 40, ["conflict"])
            openurl.assert_not_called()


    def test_router_failure_exits_nonzero_and_redacts_secrets(self):
        error = RuntimeError(
            "Configure KILO_REPAIR_TRIGGER_URL with "
            "https://hooks.kilosessions.ai/repair?token=sentinel-secret"
        )
        with patch("sys.stderr", new_callable=StringIO) as error_log:
            with self.assertRaises(SystemExit) as raised:
                m.router.fail_routing(error)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("::error::PR repair routing failed:", error_log.getvalue())
        self.assertNotIn("sentinel-secret", error_log.getvalue())



    def test_persisted_marker_precedes_dispatch_and_survives_ack_failure(self):
        events = []
        saved = []
        def api(method, path, payload=None):
            if method == "POST" and path.endswith("/comments"):
                saved.append(payload["body"])
                events.append("reservation")
                return {"id": 99}
            if method == "PATCH" and path.endswith("/comments/99"):
                events.append("acknowledgement")
                raise RuntimeError("transient GitHub failure")
            raise AssertionError((method, path, payload))

        response = MagicMock()
        response.__enter__.return_value.status = 202
        def send(*args, **kwargs):
            events.append("webhook")
            return response

        with (
            patch.object(m.router, "all_pages", return_value=[]),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "machine_contract", return_value="machine contract"),
            patch.object(m.router, "api", side_effect=api),
            patch.object(m.router.urllib.request, "urlopen", side_effect=send),
            patch.dict(os.environ, {"KILO_REPAIR_TRIGGER_URL": "https://example.invalid/repair"}),
            patch("builtins.print"),
        ):
            self.assertEqual(m.router.dispatch(self.pr, "review", ["finding"]), "requested")
        self.assertEqual(events, ["reservation", "webhook", "acknowledgement", "acknowledgement"])
        self.assertIn("<!-- kilo-auto-repair:" + "a" * 40 + ":review -->", saved[0])

        with (
            patch.object(
                m.router,
                "all_pages",
                return_value=[{"user": {"login": "github-actions[bot]"}, "body": saved[0]}],
            ),
            patch.object(m.router.urllib.request, "urlopen") as retry,
        ):
            self.assertEqual(m.router.dispatch(self.pr, "review", ["finding"]), "already-requested")
            retry.assert_not_called()



    def test_patch_reservation_retries_once(self):
        with patch.object(m.router, "api", side_effect=[RuntimeError("temporary"), {}]) as api, \
             patch("builtins.print"):
            self.assertTrue(m.router.patch_reservation(123, "marker"))
        self.assertEqual(api.call_count, 2)

    def test_explicit_http_rejection_allows_only_bounded_retry(self):
        marker = "<!-- kilo-auto-repair:" + "a" * 40 + ":review -->"
        comments = []
        events = []
        def api(method, path, payload=None):
            if method == "POST" and path.endswith("/comments"):
                comments.append({"user": {"login": "github-actions[bot]"}, "body": payload["body"]})
                events.append("reserve")
                return {"id": len(comments)}
            if method == "PATCH" and path.endswith("/issues/comments/1"):
                comments[0]["body"] = payload["body"]
                events.append("mark-rejected")
                return {}
            if method == "PATCH" and path.endswith("/issues/comments/2"):
                comments[1]["body"] = payload["body"]
                events.append("ack")
                return {}
            raise AssertionError((method, path, payload))

        accepted = MagicMock()
        accepted.__enter__.return_value.status = 202
        rejected = m.router.urllib.error.HTTPError(
            "https://example.invalid/repair", 429, "rate limited", {"Content-Type": "application/json"}, BytesIO(b"{}")
        )
        with (
            patch.object(m.router, "all_pages", side_effect=[[], comments]),
            patch.object(m.router, "api", side_effect=api),
            patch.object(m.router, "valid_kilo_webhook_url", return_value=True),
            patch.object(m.router, "machine_contract", return_value="machine contract"),
            patch.object(m.router.urllib.request, "urlopen", side_effect=[rejected, accepted]),
            patch.dict(os.environ, {"KILO_REPAIR_TRIGGER_URL": "https://example.invalid/repair"}),
            patch("builtins.print"),
        ):
            with self.assertRaises(RuntimeError):
                m.router.dispatch(self.pr, "review", ["finding"])
            self.assertIn("dispatch-state: rejected-http-429", comments[0]["body"])
            self.assertEqual(m.router.dispatch(self.pr, "review", ["finding"]), "requested")

        self.assertEqual(events, ["reserve", "mark-rejected", "reserve", "ack"])
        self.assertIn("dispatch-state: accepted", comments[1]["body"])
        self.assertEqual(len(comments), 2)


    def test_failed_kilo_handoff_persists_redacted_operational_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "pr-repair-routing.json"
            with patch.dict(os.environ, {
                "GITHUB_RUN_ID": "98765",
                "KILO_REPAIR_TRIGGER_URL": "https://example.invalid/private-trigger",
            }):
                reason = m.router.safe_route_error(
                    RuntimeError("Kilo trigger returned HTTP 403")
                )
                m.router.write_routing_evidence(
                    "failed", pr=self.pr, kind="review", reason=reason, path=target
                )
            record = json.loads(target.read_text())
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["repository"], "owner/repo")
        self.assertEqual(record["run_id"], "98765")
        self.assertEqual(record["pull_request"], 7)
        self.assertEqual(record["head_sha"], "a" * 40)
        self.assertEqual(record["kind"], "review")
        self.assertEqual(record["reason"], "Kilo trigger returned HTTP 403")
        self.assertNotIn("private-trigger", json.dumps(record))

    def test_recovery_error_classification_is_bounded_and_actionable(self):
        cases = [
            (
                RuntimeError("Kilo trigger returned HTTP 403"),
                "repair-agent-provider-error",
                403,
            ),
            (
                RuntimeError("Kilo trigger returned HTTP 503"),
                "repair-agent-provider-error",
                503,
            ),
            (
                RuntimeError("Kilo trigger could not be reached"),
                "repair-agent-unavailable",
                None,
            ),
            (
                RuntimeError("Configure KILO_REPAIR_TRIGGER_URL with an allowed webhook URL"),
                "repair-agent-configuration",
                None,
            ),
            (
                RuntimeError("https://private.invalid/path?token=do-not-print"),
                "recovery-error",
                None,
            ),
        ]
        for error, expected_code, expected_status in cases:
            detail = m.recovery_error_detail(error)
            self.assertEqual(detail["error_code"], expected_code)
            self.assertEqual(detail.get("http_status"), expected_status)
            self.assertNotIn("do-not-print", json.dumps(detail))
            self.assertNotIn("private.invalid", json.dumps(detail))
        self.assertIn("no repair completed", m.recovery_error_detail(cases[0][0])["guidance"])
        self.assertIn(
            "Provider acceptance is uncertain",
            m.recovery_error_detail(cases[1][0])["guidance"],
        )


    def test_blocker_recovery_runs_after_router_failure_without_bypassing_safety(self):
        workflow = (Path(__file__).parents[1] / "workflows/pr-issue-repair.yml").read_text()
        lines = workflow.splitlines()

        def step_body(name):
            start = lines.index("    - name: " + name)
            body = []
            for line in lines[start + 1:]:
                if line.startswith("    - name:"):
                    break
                body.append(line)
            return "\n".join(body)

        self.assertIn("      id: blocker_recovery_safety", step_body("Verify blocker recovery safety"))
        self.assertIn("      id: repair_router", step_body("Extract actionable PR findings and start Kilo repair"))
        recovery_step = step_body("Recover merge conflicts and verified review blockers")
        self.assertIn(
            "if: ${{ !cancelled() && "
            "steps.blocker_recovery_safety.outcome == 'success' && "
            "steps.repair_router.outcome != 'skipped' }}",
            recovery_step,
        )

    def test_unresolved_repair_handoff_blocks_duplicate_recovery(self):
        sha = "a" * 40
        marker = "<!-- kilo-auto-repair:" + sha + ":review -->"
        bot = {"user": {"login": "github-actions[bot]"}}
        pending = {**bot, "body": marker + "\ndispatch-state: pending"}
        accepted = {**bot, "body": marker + "\ndispatch-state: accepted"}
        rejected = {**bot, "body": marker + "\ndispatch-state: rejected-http-403"}
        stale = {**bot, "body": "<!-- kilo-auto-repair:" + "b" * 40 + ":review -->\ndispatch-state: pending"}
        forged = {"user": {"login": "contributor"}, "body": marker + "\ndispatch-state: pending"}
        self.assertTrue(m.has_unresolved_repair_handoff([pending], sha))
        self.assertTrue(m.has_unresolved_repair_handoff([accepted], sha))
        self.assertFalse(m.has_unresolved_repair_handoff([rejected], sha))
        self.assertFalse(m.has_unresolved_repair_handoff([stale], sha))
        self.assertFalse(m.has_unresolved_repair_handoff([forged], sha))

    def test_dispatch_uses_debug_for_blockers_and_code_for_review_repair(self):
        router = m.router
        base_sha = "b" * 40
        pr = {
            "number": 7,
            "html_url": "https://github.com/owner/repo/pull/7",
            "head": {"sha": "a" * 40, "ref": "fix/test"},
            "labels": [],
            "user": {"login": "owner"},
        }
        response = MagicMock()
        response.__enter__.return_value.status = 202

        def api(method, path, payload=None):
            if method == "GET" and "/commits/" in path:
                return {"sha": base_sha}
            if method == "POST":
                return {"id": 123}
            if method == "PATCH":
                return {"id": 123}
            raise AssertionError((method, path))

        def dispatch_and_check(kind, expected_url):
            with (
                patch.dict(os.environ, {
                    "KILO_DEBUG_TRIGGER_URL": "https://hooks.kilosessions.ai/debug",
                    "KILO_REPAIR_TRIGGER_URL": "https://hooks.kilosessions.ai/code",
                }),
                patch.object(router, "all_pages", return_value=[]),
                patch.object(router, "pr_details", return_value=pr),
                patch.object(router, "api", side_effect=api),
                patch.object(router.urllib.request, "urlopen", return_value=response) as urlopen,
            ):
                self.assertEqual(router.dispatch(pr, kind, []), "requested")
                self.assertEqual(urlopen.call_args.args[0].full_url, expected_url)

        dispatch_and_check("merge-conflict-" + base_sha, "https://hooks.kilosessions.ai/debug")
        dispatch_and_check("review-threads-" + base_sha, "https://hooks.kilosessions.ai/code")

if __name__ == "__main__":
    unittest.main()

class CurrentHeadFailureCoverage(unittest.TestCase):
    def setUp(self):
        self.pr = {
            "number": 17,
            "html_url": "https://github.com/owner/repo/pull/17",
            "head": {"sha": "a" * 40},
            "labels": [],
        }
        self.config = patch.multiple(m.router, REPO="owner/repo", DEFAULT="main")
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_collects_failures_from_checks_and_commit_statuses_on_exact_head(self):
        def api(method, path, payload=None):
            if "check-runs" in path:
                return {"check_runs": [
                    {"head_sha": "a" * 40, "status": "completed", "conclusion": "failure",
                     "name": "Build", "app": {"slug": "github-actions"}},
                    {"head_sha": "b" * 40, "status": "completed", "conclusion": "failure",
                     "name": "Stale", "app": {"slug": "github-actions"}},
                    {"head_sha": "a" * 40, "status": "completed", "conclusion": "success",
                     "name": "Green", "app": {"slug": "github-actions"}},
                    {"head_sha": "a" * 40, "status": "completed", "conclusion": "failure",
                     "name": "Kilo Code Review", "app": {"slug": "kilo"}},
                ]}
            if path.endswith("/status"):
                return {"statuses": [
                    {"state": "failure", "context": "Cloudflare Pages"},
                    {"state": "success", "context": "passing"},
                ]}
            raise AssertionError(path)

        with patch.object(m.router, "api", side_effect=api):
            findings = m.current_head_failure_findings(self.pr)
        self.assertEqual(len(findings), 2)
        self.assertTrue(any("github-actions/Build" in item for item in findings))
        self.assertTrue(any("Cloudflare Pages" in item for item in findings))
        self.assertFalse(any("Stale" in item or "Kilo" in item or "Green" in item for item in findings))

    def test_check_failure_dispatch_revalidates_exact_pr_head(self):
        with (
            patch.object(m.router, "pr_details", side_effect=[self.pr, {**self.pr, "head": {"sha": "b" * 40}}]),
            patch.object(m, "current_head_failure_findings", return_value=["failed check"]),
            patch.object(m.router, "dispatch") as dispatch,
        ):
            result = m.route_current_head_failures(17)
        self.assertEqual(result["state"], "changed-before-check-dispatch")
        dispatch.assert_not_called()

    def test_check_failure_dispatch_uses_debug_repair_kind(self):
        with (
            patch.object(m.router, "pr_details", return_value=self.pr),
            patch.object(m, "current_head_failure_findings", return_value=["failed check"]),
            patch.object(m.router, "dispatch", return_value="requested") as dispatch,
        ):
            result = m.route_current_head_failures(17)
        self.assertEqual(result["state"], "check-requested")
        self.assertEqual(dispatch.call_args.args[1], "check")


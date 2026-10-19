import unittest

from kilo_handoff_state import classify_http_status, next_action


class KiloHandoffStateTests(unittest.TestCase):
    def test_http_403_is_explicitly_rejected_and_allows_one_bounded_replay(self):
        rejected = classify_http_status("403")
        self.assertEqual(rejected, "rejected-http-403")
        self.assertEqual(next_action([rejected]), "dispatch:2")

    def test_two_rejections_exhaust_replay_budget(self):
        self.assertEqual(
            next_action(["rejected-http-403", "rejected-http-403"]),
            "exhausted",
        )

    def test_timeout_and_server_errors_are_uncertain_and_never_replayed(self):
        for status in ("000", "503", "not-a-status"):
            with self.subTest(status=status):
                uncertain = classify_http_status(status)
                self.assertTrue(uncertain.startswith("uncertain-"))
                self.assertEqual(next_action([uncertain]), "hold")

    def test_accepted_and_pending_markers_block_duplicate_dispatch(self):
        self.assertEqual(classify_http_status("202"), "accepted")
        self.assertEqual(next_action(["accepted"]), "accepted")
        self.assertEqual(next_action(["rejected-http-403", "accepted"]), "accepted")
        self.assertEqual(next_action(["pending"]), "hold")

    def test_duplicate_rejection_events_remain_bounded(self):
        self.assertEqual(next_action(["rejected-http-403", "rejected-http-429"]), "exhausted")

    def test_unreadable_state_fails_closed(self):
        self.assertEqual(next_action(["unknown-state"]), "hold")
        self.assertEqual(next_action([]), "dispatch:1")

    def test_closed_circuit_records_incident_and_safe_resume_needs_same_run(self):
        self.assertEqual(next_action([], circuit_breaker="closed"), "circuit-open")
        self.assertEqual(next_action(["circuit-open"], circuit_breaker="open"), "dispatch:1")
        self.assertEqual(
            next_action(["rejected-http-403", "circuit-open"], circuit_breaker="open"),
            "dispatch:2",
        )

    def test_circuit_resume_never_replays_accepted_or_uncertain_handoff(self):
        self.assertEqual(
            next_action(["accepted", "circuit-open"], circuit_breaker="open"),
            "accepted",
        )
        self.assertEqual(
            next_action(["uncertain-transport", "circuit-open"], circuit_breaker="open"),
            "hold",
        )


if __name__ == "__main__":
    unittest.main()

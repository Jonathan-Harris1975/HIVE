import contextlib
import io
import unittest
import urllib.error
from unittest.mock import MagicMock
from unittest.mock import patch

import ops_notify


class OpsNotifyHTTPErrorTests(unittest.TestCase):
    def test_http_error_logs_status_without_echoing_response_or_token(self):
        test_token = "unit-value"

        for status in (400, 403, 404):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://ops.example/events",
                    status,
                    "private response detail",
                    {},
                    None,
                )
                with (
                    patch(
                        "ops_notify._clean",
                        side_effect=["https://ops.example/events", test_token],
                    ),
                    patch("ops_notify.urllib.request.urlopen", side_effect=error),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    self.assertFalse(
                        ops_notify.send_event({"event_id": "test-event"})
                    )

                self.assertEqual(
                    stderr.getvalue().strip(),
                    f"Operational event delivery failed after 1 attempt(s): HTTP {status}",
                )
                self.assertNotIn("private response detail", stderr.getvalue())
                self.assertNotIn(test_token, stderr.getvalue())

    def test_transient_failure_retries_identical_event_once_and_then_succeeds(self):
        unavailable = urllib.error.HTTPError(
            "https://ops.example/events", 503, "private", {}, None
        )
        accepted = MagicMock()
        accepted.status = 202
        accepted.__enter__.return_value = accepted
        with (
            patch(
                "ops_notify._clean",
                side_effect=["https://ops.example/events", "unit-value"],
            ),
            patch(
                "ops_notify.urllib.request.urlopen",
                side_effect=[unavailable, accepted],
            ) as send,
            patch("ops_notify.time.sleep") as sleep,
        ):
            result = ops_notify.send_event({"event_id": "retry-stable-1"})

        self.assertTrue(result)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            send.call_args_list[0].args[0].data,
            send.call_args_list[1].args[0].data,
        )
        sleep.assert_called_once_with(0.25)

    def test_forbidden_is_not_retried(self):
        forbidden = urllib.error.HTTPError(
            "https://ops.example/events", 403, "private", {}, None
        )
        with (
            patch(
                "ops_notify._clean",
                side_effect=["https://ops.example/events", "unit-value"],
            ),
            patch("ops_notify.urllib.request.urlopen", side_effect=forbidden) as send,
            patch("ops_notify.time.sleep") as sleep,
        ):
            self.assertFalse(ops_notify.send_event({"event_id": "forbidden-1"}))
        send.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()

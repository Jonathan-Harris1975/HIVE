import contextlib
import io
import unittest
import urllib.error
from unittest.mock import patch

import ops_notify


class OpsNotifyHTTPErrorTests(unittest.TestCase):
    def test_http_error_logs_status_without_echoing_response_or_token(self):
        test_token = "unit-value"

        for status in (400, 403, 404, 500, 503):
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
                    patch("ops_notify.urllib.request.urlopen", side_effect=error) as urlopen,
                    patch("ops_notify.time.sleep"),
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    self.assertFalse(
                        ops_notify.send_event({"event_id": "test-event"})
                    )

                self.assertEqual(
                    stderr.getvalue().strip(),
                    f"Operational event delivery failed: HTTP {status}",
                )
                self.assertNotIn("private response detail", stderr.getvalue())
                self.assertNotIn(test_token, stderr.getvalue())
                expected_attempts = 3 if status in {408, 425, 429, 500, 502, 503, 504} else 1
                self.assertEqual(urlopen.call_count, expected_attempts)


if __name__ == "__main__":
    unittest.main()

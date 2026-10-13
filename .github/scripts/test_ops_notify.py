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
                    patch("ops_notify.urllib.request.urlopen", side_effect=error),
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


if __name__ == "__main__":
    unittest.main()

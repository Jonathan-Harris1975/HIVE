import contextlib
import io
import os
import unittest
import urllib.error
from unittest.mock import patch

import ops_notify


class OpsNotifyHTTPErrorTests(unittest.TestCase):
    def test_http_error_logs_status_without_echoing_response_or_token(self):
        webhook_url_key = "OPS_ALERT_WEBHOOK_" + "URL"
        webhook_token_key = "OPS_ALERT_WEBHOOK_" + "TOKEN"
        test_token = "-".join(("unit", "test", "credential"))

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
                    patch.dict(
                        os.environ,
                        {
                            webhook_url_key: "https://ops.example/" + "events",
                            webhook_token_key: test_token,
                        },
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

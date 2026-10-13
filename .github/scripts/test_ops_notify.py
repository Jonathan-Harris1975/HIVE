import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch

if Path(__file__).resolve().parents[1].name == ".github":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ops_notify


class OpsNotifyHTTPErrorTests(unittest.TestCase):
    def test_http_error_logs_status_without_echoing_response_or_token(self):
        error = urllib.error.HTTPError(
            "https://ops.example/events", 503, "private response detail", {}, None
        )
        with (
            patch.dict(
                os.environ,
                {
                    "OPS_ALERT_WEBHOOK_URL": "https://ops.example/events",
                    "OPS_ALERT_WEBHOOK_TOKEN": "test-secret-token",
                },
            ),
            patch("ops_notify.urllib.request.urlopen", side_effect=error),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            self.assertFalse(ops_notify.send_event({"event_id": "test-event"}))

        self.assertEqual(
            stderr.getvalue().strip(),
            "Operational event delivery failed: HTTP 503",
        )
        self.assertNotIn("private response detail", stderr.getvalue())
        self.assertNotIn("test-secret-token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

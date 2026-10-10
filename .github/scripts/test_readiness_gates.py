"""Exercise evidence replay and Council holds without external writes."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

import persist_ci_report_r2 as reports
import r2_result_channel as results


class ReadinessGateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            "R2_ACCOUNT_ID": "test-account", "R2_ACCESS_KEY_ID": "test-access",
            "R2_SECRET_ACCESS_KEY": "test-secret",
        }))

    def test_every_report_put_signs_create_only_condition(self):
        requests = []
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def send(request, **kwargs):
            requests.append(request)
            return Response()
        with patch.object(reports.urllib.request, "urlopen", side_effect=send):
            for key in ("report.json", "evidence-manifest.json"):
                reports.request("PUT", "https://test.r2.cloudflarestorage.com", key, b"{}", "application/json")
            reports.request("HEAD", "https://test.r2.cloudflarestorage.com", "report.json")
        for request in requests[:2]:
            self.assertEqual(request.get_header("If-none-match"), "*")
            self.assertIn("if-none-match", request.get_header("Authorization"))
        self.assertIsNone(requests[2].get_header("If-none-match"))

    def test_report_replay_is_rejected_and_error_body_not_logged(self):
        failure = urllib.error.HTTPError("https://r2.invalid", 412, "exists", {}, io.BytesIO(b"private-details"))
        with patch.object(reports.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "HTTP 412") as error:
                reports.request("PUT", "https://test.r2.cloudflarestorage.com", "manifest.json", b"{}")
        self.assertNotIn("private-details", str(error.exception))

    def test_result_upload_cannot_omit_replay_condition(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(results.presign_put("bucket", "exact-run.json", 600)).query)
        self.assertEqual(query["X-Amz-SignedHeaders"], ["content-type;host;if-none-match"])

    def test_completed_council_hold_and_empty_ready_deny_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "council.json"
            for disposition, evidence, blockers in (
                ("HOLD", ["check run"], ["provider unavailable"]),
                ("PENDING_MINIMUM_AGE", ["check run"], []),
                ("READY", [], []),
                ("READY", ["check run"], ["unresolved failure"]),
                ("READY", [""], []),
            ):
                with self.subTest(disposition=disposition, evidence=evidence, blockers=blockers):
                    path.write_text(json.dumps({"disposition": disposition, "evidence": evidence, "blockers": blockers}))
                    with self.assertRaises(RuntimeError): results.require_ready(path)
            path.write_text(json.dumps({"disposition": "READY", "evidence": ["exact-SHA verified check"], "blockers": []}))
            results.require_ready(path)

    def test_numeric_certification_is_not_boolean_certification(self):
        payload = {"kind":"council-final", "repository":"owner/repo", "sha":"a"*40,
                   "run_id":123, "run_attempt":1, "certification_complete":1,
                   "disposition":"READY", "evidence":[], "blockers":[]}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"council.json"
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(RuntimeError,"certification_complete"):
                results.validate_result(path,"owner/repo","a"*40,123,1)


if __name__ == "__main__":
    unittest.main()

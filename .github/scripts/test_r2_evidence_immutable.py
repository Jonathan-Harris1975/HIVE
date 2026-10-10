"""Regression tests for signed conditional creation of R2 evidence objects."""
import importlib.util
from pathlib import Path
import os
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("r2_evidence_store", Path(__file__).with_name("r2_evidence_store.py"))
store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(store)


class ImmutableEvidenceTests(unittest.TestCase):
    def test_create_only_condition_is_signed(self):
        credentials = {
            "R2_ACCESS_KEY_ID": "test-access",
            "R2_SECRET_ACCESS_KEY": "test-secret",
        }
        with patch.dict(os.environ, credentials):
            request = store._signed_request(
                "PUT", "https://example.com/bucket/evidence.json",
                b'{"kind":"test"}', "application/json", create_only=True
            )
        self.assertEqual(request.get_header("If-none-match"), "*")
        self.assertIn("if-none-match", request.get_header("Authorization"))

    def test_unconditional_put_is_not_create_only(self):
        with patch.dict(os.environ, {"R2_ACCESS_KEY_ID": "test", "R2_SECRET_ACCESS_KEY": "test"}):
            request = store._signed_request("PUT", "https://example.com/bucket/test", b"{}")
        self.assertIsNone(request.get_header("If-none-match"))


if __name__ == "__main__":
    unittest.main()

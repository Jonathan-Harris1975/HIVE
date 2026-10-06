#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).with_name("r2_evidence_store.py")
SPEC = importlib.util.spec_from_file_location("r2_evidence_store", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R2EvidenceStoreTests(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "dast",
            "repository": "Jonathan-Harris1975/HIVE",
            "sha": "a" * 40,
            "run_id": 12345,
            "run_attempt": 2,
            "scan_outcome": "success",
        }

    def test_exact_run_key_is_put_and_verified_without_secret_in_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(self.payload()), encoding="utf-8")
            requests: list[object] = []

            def capture(request, expected):
                requests.append((request, expected))

            env = {
                "R2_ACCOUNT_ID": "account123",
                "R2_ACCESS_KEY_ID": "access-secret-marker",
                "R2_SECRET_ACCESS_KEY": "private-secret-marker",
                "R2_REGION": "auto",
            }
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(
                MODULE, "_request", side_effect=capture
            ), mock.patch.object(sys, "argv", ["r2_evidence_store.py", str(path)]):
                self.assertEqual(MODULE.main(), 0)

            self.assertEqual(len(requests), 2)
            put_request = requests[0][0]
            head_request = requests[1][0]
            self.assertEqual(put_request.method, "PUT")
            self.assertEqual(head_request.method, "HEAD")
            expected_key = (
                "/hive-repositories/ci-evidence/"
                "Jonathan-Harris1975__HIVE/dast/"
                + "a" * 40
                + "/12345-2.json"
            )
            self.assertTrue(put_request.full_url.endswith(expected_key))
            self.assertNotIn("access-secret-marker", put_request.full_url)
            self.assertNotIn("private-secret-marker", put_request.full_url)

    def test_missing_run_binding_is_rejected_before_network(self) -> None:
        payload = self.payload()
        del payload["run_attempt"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["r2_evidence_store.py", str(path)]):
                with self.assertRaisesRegex(RuntimeError, "run_attempt"):
                    MODULE.main()


if __name__ == "__main__":
    unittest.main()

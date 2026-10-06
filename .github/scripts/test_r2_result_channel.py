from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("r2_result_channel.py")
SPEC = importlib.util.spec_from_file_location("r2_result_channel", SCRIPT)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class R2ResultChannelTests(unittest.TestCase):
    def test_path_rejects_traversal(self) -> None:
        with self.assertRaises(RuntimeError):
            M._encode_path("hive-repositories", "ci-evidence/../secret")

    def test_presign_is_scoped_to_exact_object(self) -> None:
        old = dict(os.environ)
        try:
            os.environ.update({
                "R2_ACCOUNT_ID": "acct",
                "R2_ACCESS_KEY_ID": "access",
                "R2_SECRET_ACCESS_KEY": "secret",
            })
            url = M.presign_put("hive-repositories", "ci-evidence/repo/council-final/abc.json", 600)
            self.assertIn("/hive-repositories/ci-evidence/repo/council-final/abc.json?", url)
            self.assertIn("X-Amz-Expires=600", url)
            self.assertNotIn("secret", url)
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_validate_exact_council_result(self) -> None:
        payload = {
            "kind": "council-final",
            "repository": "Jonathan-Harris1975/HIVE",
            "sha": "a" * 40,
            "run_id": 123,
            "run_attempt": 1,
            "certification_complete": True,
            "disposition": "READY",
            "evidence": [],
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            M.validate_result(path, payload["repository"], payload["sha"], 123, 1)

    def test_validate_rejects_wrong_run(self) -> None:
        payload = {
            "kind": "council-final",
            "repository": "Jonathan-Harris1975/HIVE",
            "sha": "a" * 40,
            "run_id": 999,
            "run_attempt": 1,
            "certification_complete": True,
            "disposition": "READY",
            "evidence": [],
            "blockers": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                M.validate_result(path, payload["repository"], payload["sha"], 123, 1)


if __name__ == "__main__":
    unittest.main()

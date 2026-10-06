from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("r2_evidence.py")
SPEC = importlib.util.spec_from_file_location("r2_evidence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class R2EvidenceTests(unittest.TestCase):
    def test_normalise_key_accepts_bound_evidence_prefix(self) -> None:
        key = "evidence/Jonathan-Harris1975/HIVE/abc/dast/123/1/report.json"
        self.assertEqual(MODULE._normalise_key(key), key)

    def test_normalise_key_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            MODULE._normalise_key("evidence/HIVE/../secret")

    def test_missing_credentials_fail_closed(self) -> None:
        old_env = dict(MODULE.os.environ)
        try:
            for name in (
                "R2_ACCOUNT_ID",
                "CF_R2_ACCOUNT_ID",
                "R2_ACCESS_KEY_ID",
                "CF_R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "CF_R2_SECRET_ACCESS_KEY",
            ):
                MODULE.os.environ.pop(name, None)
            with tempfile.NamedTemporaryFile() as handle:
                with self.assertRaisesRegex(RuntimeError, "Missing required R2 credential environment"):
                    MODULE.put_object(
                        file_path=handle.name,
                        object_key="evidence/HIVE/test.json",
                        content_type="application/json",
                        bucket="hive-repositories",
                    )
        finally:
            MODULE.os.environ.clear()
            MODULE.os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()

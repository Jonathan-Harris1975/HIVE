from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import datetime as dt

SCRIPT = Path(__file__).with_name("r2_mutation_lease.py")
SPEC = importlib.util.spec_from_file_location("r2_mutation_lease", SCRIPT)
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class R2MutationLeaseTests(unittest.TestCase):
    def test_same_fingerprint_maps_to_same_key(self) -> None:
        a = M._key_for("Jonathan-Harris1975/HIVE", "ci|path|sha")
        b = M._key_for("Jonathan-Harris1975/HIVE", "ci|path|sha")
        self.assertEqual(a, b)

    def test_repository_changes_key(self) -> None:
        a = M._key_for("Jonathan-Harris1975/HIVE", "ci|path|sha")
        b = M._key_for("Jonathan-Harris1975/IRS", "ci|path|sha")
        self.assertNotEqual(a, b)

    def test_invalid_owner_rejected_before_network(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "owner"):
            M.acquire("Jonathan-Harris1975/HIVE", "x", "someone-else", 300, "hive-repositories")


    def test_renew_rejects_expired_lease_without_writing(self) -> None:
        expired = {
            "repository": "Jonathan-Harris1975/HIVE",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "token",
            "expires_at": "2020-01-01T00:00:00Z",
            "generation": 1,
        }
        with mock.patch.object(M, "_read", return_value=(expired, '"etag"')), mock.patch.object(M, "_put") as put:
            with self.assertRaisesRegex(RuntimeError, "expired"):
                M.renew("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "token", 300, "bucket")
            put.assert_not_called()

    def test_renew_requires_owner_token_and_uses_conditional_etag(self) -> None:
        current = {
            "repository": "Jonathan-Harris1975/HIVE",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "secret-token",
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(),
            "generation": 4,
        }
        with mock.patch.object(M, "_read", return_value=(current, '"etag"')), mock.patch.object(M, "_put", return_value='"new"') as put:
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                M.renew("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "wrong", 300, "bucket")
            put.assert_not_called()
            result = M.renew("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", 300, "bucket")
            self.assertEqual(result["generation"], 4)
            self.assertEqual(put.call_args.args[3], {"if-match": '"etag"'})

    def test_renew_fails_closed_on_etag_race(self) -> None:
        current = {
            "repository": "Jonathan-Harris1975/HIVE",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "secret-token",
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(),
        }
        with mock.patch.object(M, "_read", return_value=(current, '"etag"')), mock.patch.object(M, "_put", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "concurrent race"):
                M.renew("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", 300, "bucket")


    def test_release_rejects_expired_holder(self) -> None:
        current = {
            "repository": "Jonathan-Harris1975/HIVE",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "secret-token",
            "expires_at": "2020-01-01T00:00:00Z",
        }
        with mock.patch.object(M, "_read", return_value=(current, '"etag"')), mock.patch.object(M, "_put") as put:
            with self.assertRaisesRegex(RuntimeError, "expired lease"):
                M.release("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", "bucket")
            put.assert_not_called()

    def test_release_rejects_mismatched_identity(self) -> None:
        current = {
            "repository": "different/repository",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "secret-token",
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(),
        }
        with mock.patch.object(M, "_read", return_value=(current, '"etag"')), mock.patch.object(M, "_put") as put:
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                M.release("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", "bucket")
            put.assert_not_called()


    def test_validate_current_generation_and_reject_stale(self) -> None:
        current = {
            "repository": "Jonathan-Harris1975/HIVE",
            "fingerprint_sha256": M.hashlib.sha256(b"fingerprint").hexdigest(),
            "owner": "kilo",
            "token": "secret-token",
            "generation": 5,
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).isoformat(),
        }
        with mock.patch.object(M, "_read", return_value=(current, '"etag"')):
            self.assertTrue(M.validate("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", 5, "bucket")["valid"])
            with self.assertRaisesRegex(RuntimeError, "stale lease generation"):
                M.validate("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", 4, "bucket")

    def test_validate_missing_lease_fails_closed(self) -> None:
        with mock.patch.object(M, "_read", return_value=(None, None)):
            with self.assertRaisesRegex(RuntimeError, "missing or unreadable"):
                M.validate("Jonathan-Harris1975/HIVE", "fingerprint", "kilo", "secret-token", 1, "bucket")


if __name__ == "__main__":
    unittest.main()

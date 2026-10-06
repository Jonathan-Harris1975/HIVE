from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()

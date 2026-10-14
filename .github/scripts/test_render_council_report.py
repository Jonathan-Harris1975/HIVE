from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("render_council_report.py")
SPEC = importlib.util.spec_from_file_location("render_council_report", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CouncilReportRendererTests(unittest.TestCase):
    def payload(self):
        return {
            "kind": "council-final",
            "repository": "Jonathan-Harris1975/HIVE",
            "sha": "a" * 40,
            "run_id": 123,
            "run_attempt": 2,
            "certification_complete": True,
            "disposition": "HOLD",
            "evidence": ["Exact-SHA CI checks completed"],
            "blockers": ["[provider unavailable](https://example.invalid)"],
        }

    def test_renders_readable_report_for_the_validated_run(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "result.json"
            output = Path(directory) / "council-report.md"
            source.write_text(json.dumps(self.payload()), encoding="utf-8")
            MODULE.validate_result(source, "Jonathan-Harris1975/HIVE", "a" * 40, 123, 2)
            output.write_text(MODULE.render_report(json.loads(source.read_text())), encoding="utf-8")
            report = output.read_text(encoding="utf-8")
            self.assertIn("Repository Council report", report)
            self.assertIn("Evaluated default-branch SHA: " + "a" * 40, report)
            self.assertIn("Disposition: HOLD", report)
            self.assertIn("Exact-SHA CI checks completed", report)
            self.assertIn(r"\[provider unavailable\]", report)

    def test_report_rejects_non_string_evidence(self):
        payload = self.payload()
        payload["evidence"] = [123]
        with self.assertRaisesRegex(RuntimeError, "must be strings"):
            MODULE.render_report(payload)

    def test_report_rejects_empty_evidence_entries(self):
        payload = self.payload()
        payload["evidence"] = ["  "]
        with self.assertRaisesRegex(RuntimeError, "non-empty"):
            MODULE.render_report(payload)


if __name__ == "__main__":
    unittest.main()

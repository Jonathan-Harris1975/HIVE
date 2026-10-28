import copy
import datetime as dt
import json
import unittest
from types import SimpleNamespace

from app.services.llm_spend import read_spend_report, summarise_report

NOW = dt.datetime(2026, 10, 28, 8, tzinfo=dt.timezone.utc)


def report():
    return {"ok": True, "reconciled": True, "provider": "OpenRouter", "currency": "USD",
            "period": {"date": "2026-10-27", "timezone": "Europe/London",
                       "start": "2026-10-27T00:00:00Z", "end": "2026-10-28T00:00:00Z"},
            "collectedAt": "2026-10-28T07:05:00Z", "recordedSpendUsd": 2,
            "totals": {"total_usage": 2, "request_count": 4},
            "breakdown": [{"model": "Luna", "app": "private-id", "total_usage": 1, "request_count": 2}]*2}


class SpendTests(unittest.TestCase):
    def test_verified_report_groups_models_without_app_identifiers_or_invented_gbp(self):
        result = summarise_report(report(), NOW)
        self.assertEqual(result["models"], [{"model": "Luna", "usd": 2, "requests": 4}])
        self.assertEqual(result["tokens"], {})
        self.assertIsNone(result["gbp"])
        self.assertTrue(result["fresh"])
        self.assertNotIn("private-id", json.dumps(result))

    def test_malformed_and_unreconciled_evidence_is_rejected(self):
        for field, value in [("reconciled", False), ("recordedSpendUsd", None), ("recordedSpendUsd", True),
                             ("recordedSpendUsd", float("nan")), ("breakdown", [])]:
            raw = copy.deepcopy(report())
            raw[field] = value
            with self.assertRaises(ValueError):
                summarise_report(raw, NOW)
        raw = report()
        raw["period"]["start"] = "2026-10-27T01:00:00Z"
        with self.assertRaises(ValueError):
            summarise_report(raw, NOW)

    def test_missing_data_is_unavailable_not_zero_and_paths_are_bounded(self):
        class Missing:
            def read_object(self, key, **kwargs):
                self.key = key
                self.options = kwargs
                raise FileNotFoundError()
        storage = Missing()
        result = read_spend_report(storage, "metasystem", now=NOW)
        self.assertFalse(result["available"])
        self.assertIsNone(result["report"])
        self.assertEqual(storage.key, "reports/openrouter-daily/latest.json")
        self.assertTrue(storage.options["read_only"])
        for date in ["../../keys", "2026-02-30", "2026-10-28"]:
            with self.assertRaises(ValueError):
                read_spend_report(storage, "metasystem", date, NOW)

    def test_missing_comparisons_do_not_become_savings(self):
        class Storage:
            def read_object(self, key, **kwargs):
                if key.endswith("latest.json"):
                    return SimpleNamespace(content=json.dumps(report()).encode())
                raise FileNotFoundError()
        result = read_spend_report(Storage(), "metasystem", now=NOW)
        self.assertTrue(result["available"])
        self.assertTrue(all(not row["available"] for row in result["comparisons"]))
        self.assertFalse(result["quality_evidence_available"])
        self.assertFalse(result["task_attribution_available"])

    def test_gbp_requires_dated_reconciled_fx_evidence(self):
        raw = report()
        raw["gbp"] = {"estimate": 1.5, "rate": 0.75, "rateDate": "2026-10-27", "source": "test-only"}
        self.assertEqual(summarise_report(raw, NOW)["gbp"]["estimate"], 1.5)
        raw["gbp"]["estimate"] = 2
        with self.assertRaises(ValueError):
            summarise_report(raw, NOW)

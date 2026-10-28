from types import SimpleNamespace
import unittest

from app.services.model_cost_policy import prefer_evaluated_cost


def candidate(name, cost, **changes):
    values = dict(model_id=name, cost_per_successful_task=cost, available=True,
                  evaluation_passed=True, evaluation_sample_size=50,
                  evaluation_evidence="eval://summary-golden-set")
    values.update(changes)
    return SimpleNamespace(**values)


class EconomySelectionTests(unittest.TestCase):
    def test_prefers_measured_successful_task_cost(self):
        costly = candidate("costly", 0.1)
        cheap = candidate("cheap", 0.01)
        self.assertEqual(prefer_evaluated_cost([costly, cheap]), [cheap, costly])

    def test_missing_or_invalid_evidence_cannot_promote_a_challenger(self):
        baseline = candidate("baseline", 0.1)
        for changes in [dict(available=False), dict(available=None),
                        dict(evaluation_passed=False), dict(evaluation_sample_size=0),
                        dict(evaluation_evidence=""), dict(cost_per_successful_task=None),
                        dict(cost_per_successful_task=float("nan")),
                        dict(cost_per_successful_task=float("inf")),
                        dict(cost_per_successful_task=-1), dict(cost_per_successful_task=True)]:
            with self.subTest(changes=changes):
                challenger = candidate("challenger", 0, **changes)
                self.assertEqual(prefer_evaluated_cost([baseline, challenger])[0], baseline)

    def test_preserves_legacy_order_and_equal_cost_ties(self):
        first = candidate("first", None, evaluation_passed=None)
        second = candidate("second", None, evaluation_passed=None)
        self.assertEqual(prefer_evaluated_cost([first, second]), [first, second])
        self.assertEqual(prefer_evaluated_cost([candidate("a", 0), candidate("b", 0)])[0].model_id, "a")


if __name__ == "__main__":
    unittest.main()

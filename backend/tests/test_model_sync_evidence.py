from __future__ import annotations

from app.services.model_sync import _aims_explicit_assignments


def test_aims_route_assignments_choose_only_available_evaluated_approved_models():
    registry = {
        "fast": [
            {
                "model_id": "model/cheap-passed",
                "score": 0.9,
                "available": True,
                "evaluation_passed": True,
                "evaluation_sample_size": 20,
                "evaluation_evidence": "eval/fast.json",
                "cost_per_successful_task": 0.01,
                "approved_roles": ["AI_MODEL_FAST"],
            },
            {
                "model_id": "model/unavailable-cheaper",
                "available": False,
                "evaluation_passed": True,
                "evaluation_sample_size": 20,
                "evaluation_evidence": "eval/unavailable.json",
                "cost_per_successful_task": 0.001,
                "approved_roles": ["AI_MODEL_FAST"],
            },
            {
                "model_id": "model/untested",
                "available": True,
                "evaluation_passed": None,
                "approved_roles": ["AI_MODEL_FAST"],
            },
        ]
    }

    assert _aims_explicit_assignments(registry) == {"AI_MODEL_FAST": "model/cheap-passed"}

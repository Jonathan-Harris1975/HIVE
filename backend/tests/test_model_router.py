from app.core.config import Settings
from app.services.model_router import Mode, ModelRouter, TaskType


def test_model_router_detects_audit() -> None:
    router = ModelRouter(Settings())
    assert router.classify_task("Check AIMS quarantine retry logic", Mode.AUTO) == TaskType.AUDIT


def test_requested_model_overrides_default() -> None:
    router = ModelRouter(Settings())
    assert router.select_model(TaskType.GENERAL, "openai/gpt-test") == "openai/gpt-test"

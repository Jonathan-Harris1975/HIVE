from app.core.config import Settings
from app.services.model_router import Mode, ModelRouter, TaskType


def test_model_router_detects_audit() -> None:
    router = ModelRouter(Settings())
    assert router.classify_task("Check AIMS quarantine retry logic", Mode.AUTO) == TaskType.AUDIT


def test_requested_model_overrides_default() -> None:
    router = ModelRouter(Settings())
    assert router.select_model(TaskType.GENERAL, "openai/gpt-test") == "openai/gpt-test"


def test_brand_mode_routes_to_audit_model() -> None:
    settings = Settings(audit_model="audit-test-model")
    router = ModelRouter(settings)
    task = router.classify_task("Give me an on-brand HIVE summary", Mode.BRAND)
    assert task == TaskType.AUDIT
    assert router.select_model(task) == "audit-test-model"


def test_fallbacks_exclude_selected_model() -> None:
    settings = Settings(default_model="model-a", cheap_model="model-b", balanced_model="model-c")
    router = ModelRouter(settings)
    fallbacks = router.fallback_models_for_task(TaskType.GENERAL, "model-a")
    assert "model-a" not in fallbacks
    assert fallbacks[0] == "model-b"

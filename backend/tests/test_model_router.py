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
    settings = Settings(
        default_model="model-a",
        cheap_model="model-b:free",
        balanced_model="model-c",
        openrouter_free_fallback_model="model-free:free",
    )
    router = ModelRouter(settings)
    fallbacks = router.fallback_models_for_task(TaskType.GENERAL, "model-a")
    assert "model-a" not in fallbacks
    assert fallbacks == ["model-free:free", "model-b:free"]


def test_paid_fallbacks_are_blocked_by_default() -> None:
    settings = Settings(
        default_model="paid-default",
        cheap_model="free-cheap:free",
        balanced_model="paid-balanced",
        openrouter_free_fallback_model="free-fallback:free",
    )
    router = ModelRouter(settings)
    assert router.fallback_models_for_task(TaskType.GENERAL, "dead-model") == [
        "free-fallback:free",
        "free-cheap:free",
    ]


def test_paid_fallbacks_can_be_enabled() -> None:
    settings = Settings(
        default_model="paid-default",
        cheap_model="free-cheap:free",
        balanced_model="paid-balanced",
        openrouter_free_fallback_model="free-fallback:free",
        allow_paid_fallback=True,
    )
    router = ModelRouter(settings)
    assert router.fallback_models_for_task(TaskType.GENERAL, "dead-model") == [
        "free-fallback:free",
        "free-cheap:free",
        "paid-default",
        "paid-balanced",
    ]


def test_auto_mode_resolves_aims_rams_to_audit_prompt_mode() -> None:
    router = ModelRouter(Settings())
    task = router.classify_task("Check RAMS audit wording for AIMS", Mode.AUTO)
    assert task == TaskType.AUDIT
    assert router.resolve_mode(task, Mode.AUTO) == Mode.AUDIT

from app.core.config import Settings
from app.services.model_router import Mode, ModelRouter, TaskType


def test_catalogue_rankings_do_not_override_configured_summary_or_code_models(monkeypatch):
    from types import SimpleNamespace

    def candidate(name, cost, score=0.95, **changes):
        values = dict(model_id=name, score=score, lifecycle_status="active", available=True,
                      evaluation_passed=True, evaluation_sample_size=50,
                      evaluation_evidence="eval://summary", cost_per_successful_task=cost)
        values.update(changes)
        return SimpleNamespace(**values)

    models = [candidate("strong-baseline", 0.10), candidate("qualified-economy", 0.01),
              candidate("below-floor", 0, score=0.1),
              candidate("failed-eval", 0, evaluation_passed=False),
              candidate("retired", 0, lifecycle_status="retired")]
    monkeypatch.setattr("app.services.model_router.model_registry.get_ranked_models", lambda _: models)
    router = ModelRouter(Settings(_env_file=None))
    assert router.select_model(TaskType.SUMMARY) == router.settings.cheap_model
    assert router.select_model(TaskType.CODE) == router.settings.code_model


def test_summary_without_task_cost_evidence_keeps_operator_defaults(monkeypatch):
    from types import SimpleNamespace

    models = [SimpleNamespace(model_id=name, score=0.95, lifecycle_status="active")
              for name in ["existing", "challenger"]]
    monkeypatch.setattr("app.services.model_router.model_registry.get_ranked_models", lambda _: models)
    settings = Settings(_env_file=None)
    assert ModelRouter(settings).select_model(TaskType.SUMMARY) == settings.cheap_model


def test_title_model_stays_configured_even_when_catalogue_evidence_changes(monkeypatch):
    from types import SimpleNamespace

    def candidate(name, cost, **changes):
        values = dict(model_id=name, score=0.95, lifecycle_status="active", available=True,
                      evaluation_passed=True, evaluation_sample_size=50,
                      evaluation_evidence="eval://title", cost_per_successful_task=cost,
                      approved_roles=("conversation_auto_title",))
        values.update(changes)
        return SimpleNamespace(**values)

    baseline = candidate("paid-baseline", 0.10)
    economy = candidate("paid-economy", 0.01)
    models = [baseline, economy, candidate("public-model:free", 0),
              candidate("openrouter/free", 0), candidate("anthropic/claude-opus-5.5", 0),
              candidate("wrong-role", 0, approved_roles=("coding",))]
    monkeypatch.setattr("app.services.model_router.model_registry.get_ranked_models", lambda _: models)
    router = ModelRouter(Settings(_env_file=None, cheap_model="paid-baseline"))
    assert router.select_title_model() == "paid-baseline"
    baseline.evaluation_evidence = ""
    assert router.select_title_model() == "paid-baseline"
    baseline.evaluation_evidence = "eval://title"
    economy.cost_per_successful_task = 0.20
    assert router.select_title_model() == "paid-baseline"


def test_model_defaults_are_pinned_and_routes_keep_distinct_roles(monkeypatch) -> None:
    for name in (
        "DEFAULT_MODEL",
        "CHEAP_MODEL",
        "BALANCED_MODEL",
        "PREMIUM_MODEL",
        "CODE_MODEL",
        "AUDIT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    monkeypatch.setattr(
        "app.services.model_router.model_registry.get_ranked_models",
        lambda _category: [],
    )
    router = ModelRouter(settings)

    assert router.select_model(TaskType.GENERAL) == "openai/gpt-5.6-luna"
    assert router.select_model(TaskType.SUMMARY) == "openai/gpt-5.6-luna"
    assert router.select_model(TaskType.FILE_TRIAGE) == "anthropic/claude-sonnet-5.5"
    assert router.select_model(TaskType.AUDIT) == "anthropic/claude-sonnet-5.5"
    assert router.select_model(TaskType.CODE) == "openai/gpt-5.6-sol"
    assert router.select_model(TaskType.PREMIUM) == "anthropic/claude-opus-5.5"


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
        "paid-default",
        "paid-balanced",
        "free-fallback:free",
        "free-cheap:free",
    ]


def test_auto_mode_resolves_aims_rams_to_audit_prompt_mode() -> None:
    router = ModelRouter(Settings())
    task = router.classify_task("Check RAMS audit wording for AIMS", Mode.AUTO)
    assert task == TaskType.AUDIT
    assert router.resolve_mode(task, Mode.AUTO) == Mode.AUDIT


def test_audit_signal_takes_precedence_over_repo_signal() -> None:
    router = ModelRouter(Settings())
    assert router.classify_task("Audit this repo governance gate", Mode.AUTO) == TaskType.AUDIT


def test_progressive_code_models_are_unique_bounded_and_non_premium() -> None:
    settings = Settings(
        cheap_model="cheap-coder",
        balanced_model="balanced-coder",
        code_model="strong-coder",
        premium_model="premium-expert",
    )
    router = ModelRouter(settings)

    ladder = router.progressive_models_for_task(TaskType.CODE, max_models=4)

    assert ladder == ["cheap-coder", "balanced-coder", "strong-coder"]
    assert "premium-expert" not in ladder


def test_council_model_ladder_has_hard_two_run_ceiling() -> None:
    settings = Settings(
        code_model="code-reviewer",
        audit_model="audit-reviewer",
        premium_model="premium-expert",
    )
    router = ModelRouter(settings)

    ladder = router.council_models_for_task(TaskType.CODE, max_models=99)

    assert len(ladder) == 2
    assert ladder == ["audit-reviewer", "code-reviewer"]
    assert "premium-expert" not in ladder

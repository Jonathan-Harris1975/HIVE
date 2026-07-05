from __future__ import annotations

from app.services.env_audit import audit_environment


def test_audit_environment_against_real_env_example_returns_valid_ratio():
    result = audit_environment()
    assert result["env_example_found"] is True
    assert result["total_settings_fields"] > 0
    assert 0.0 <= result["coverage_ratio"] <= 1.0
    # Every field this programme itself added across phases 1-13 should be
    # documented; this doesn't assert 100% coverage overall since some
    # pre-existing fields may be internal/computed rather than env-driven.
    for field_name in (
        "repository_ttl_seconds",
        "ai_search_instance",
        "model_registry_seed_json",
        "provider_framework_extra_providers_json",
        "benchmark_weights_json",
        "ai_council_promotion_threshold",
        "github_token",
    ):
        undocumented_names = {item["field"] for item in result["undocumented_fields"]}
        assert field_name not in undocumented_names


def test_audit_environment_detects_undocumented_and_extra_vars(tmp_path):
    env_example = tmp_path / ".env.example"
    env_example.write_text("SOME_UNRELATED_VAR=value\n# a comment\n\nOPENROUTER_API_KEY=\n")

    result = audit_environment(env_example_path=env_example)

    assert result["env_example_found"] is True
    assert "SOME_UNRELATED_VAR" in result["extra_in_env_example"]
    assert result["documented_field_count"] >= 1


def test_audit_environment_handles_missing_env_example_gracefully(tmp_path):
    missing_path = tmp_path / "does-not-exist.env"
    result = audit_environment(env_example_path=missing_path)
    assert result["env_example_found"] is False
    assert result["documented_field_count"] == 0
    assert result["undocumented_field_count"] == result["total_settings_fields"]

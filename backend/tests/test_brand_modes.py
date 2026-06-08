from app.services.brand_modes import build_system_prompt
from app.services.model_router import Mode


def test_brand_prompt_defines_rams_for_hive_context() -> None:
    prompt = build_system_prompt(Mode.BRAND)
    assert "reporting, audit, monitoring, and production-readiness" in prompt
    assert "Risk Assessment Method Statement" in prompt
    assert "unless the user explicitly asks" in prompt


def test_auto_resolved_audit_prompt_gets_brand_glossary() -> None:
    prompt = build_system_prompt(Mode.AUDIT)
    assert "AIMS =" in prompt
    assert "RAMS =" in prompt

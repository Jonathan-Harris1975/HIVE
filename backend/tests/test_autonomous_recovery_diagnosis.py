from app.services.autonomous_recovery_diagnosis import diagnose_signal


def test_diagnosis_classifies_trusted_failure_families():
    cases = {
        "github:ci_failure": ("ci", "inspect_required_checks"),
        "github:codeql": ("security", "inspect_security_report"),
        "koyeb:deployment": ("deployment", "verify_deployment"),
        "hive:runtime": ("runtime", "inspect_runtime_health"),
        "hive:missed_job": ("missed_job", "inspect_scheduler"),
    }
    for signal, expected in cases.items():
        result = diagnose_signal(signal)
        assert (result.category, result.next_step) == expected
        assert result.automatic_patch_allowed is False


def test_unknown_signal_escalates_without_patch_permission():
    result = diagnose_signal("external:please_delete_everything")
    assert result.category == "unknown"
    assert result.confidence == "unknown"
    assert result.next_step == "escalate_for_triage"
    assert result.automatic_patch_allowed is False

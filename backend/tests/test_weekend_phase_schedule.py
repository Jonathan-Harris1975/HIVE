from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / ".github" / "workflows" / "weekend-phase-launcher.yml"
TRUSTED_AUTOMATION = ROOT / ".github" / "workflows" / "trusted-automation.yml"


def test_every_weekend_phase_cron_has_a_dst_resolver_arm() -> None:
    workflow = LAUNCHER.read_text(encoding="utf-8")
    scheduled = set(re.findall(r'^\s*- cron: "([^"]+)"\s*$', workflow, re.MULTILINE))
    routed = set(re.findall(r'"([^"]+):\+(?:0100|0000)"', workflow))

    assert scheduled == routed


def test_dast_schedule_resolves_to_0221_london_in_both_dst_states() -> None:
    """The UTC cron arms must resolve to one 02:21 Europe/London wall-clock slot."""
    workflow = LAUNCHER.read_text(encoding="utf-8")

    assert '- cron: "21 1 1 * *"' in workflow
    assert '- cron: "21 2 1 * *"' in workflow
    assert '"21 1 1 * *:+0100"|"21 2 1 * *:+0000"' in workflow
    assert "DAST: monthly on the 1st at 02:21 Europe/London" in workflow


def test_trusted_weekend_sweeps_keep_two_reconciliation_slots_per_hour() -> None:
    workflow = TRUSTED_AUTOMATION.read_text(encoding="utf-8")

    assert '- cron: "12,42 22-23 * * 6"' in workflow
    assert '- cron: "12,42 0-5 * * 0"' in workflow

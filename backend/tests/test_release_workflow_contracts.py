from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
WATCHER_PATH = ROOT / ".github/workflows/koyeb-deployment-watch.yml"
CI_PATH = ROOT / ".github/workflows/ci.yml"


def _step(text: str, name: str) -> str:
    match = re.search(
        rf"      - name: {re.escape(name)}\n(?P<body>[\s\S]*?)(?=\n      - (?:name:|uses:|if:)|\n\n  [a-zA-Z_]|\Z)",
        text,
    )
    assert match is not None, f"workflow step not found: {name}"
    return match.group("body")


def test_production_watcher_fails_closed_without_koyeb_configuration() -> None:
    text = WATCHER_PATH.read_text(encoding="utf-8")
    config = _step(text, "Check Koyeb deployment-watch configuration")

    assert "for name in KOYEB_TOKEN KOYEB_SERVICE" in config
    assert "exit 1" in config
    assert "::error::HIVE production deployment verification cannot run" in config
    assert "::warning::" not in config
    assert "configured=false" not in config
    assert "skipped" not in config.lower()


def test_exact_sha_watch_gates_attestation_evidence_and_smoke_dispatch() -> None:
    text = WATCHER_PATH.read_text(encoding="utf-8")
    watch = text.index("- name: Watch production deployment")
    attestation = text.index("- name: Record exact-SHA production deployment attestation")
    evidence = text.index("- name: Retain production deployment evidence")
    dispatch = text.index("- name: Trigger central ecosystem smoke")

    assert watch < attestation < evidence < dispatch
    assert "EXPECTED_DEPLOYMENT_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in _step(
        text, "Watch production deployment"
    )
    assert "DEPLOYED_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}" in _step(
        text, "Record exact-SHA production deployment attestation"
    )
    assert "deployment_config.outputs.configured" not in text


def test_ci_scans_the_built_hive_image_and_retains_the_report() -> None:
    text = CI_PATH.read_text(encoding="utf-8")
    scan = _step(text, "Scan built production image for fixable high-severity vulnerabilities")
    evidence = _step(text, "Retain production image vulnerability report")

    assert "docker build --target runtime -t hive:ci" in text
    assert ".ci-tools/bin/trivy image hive:ci" in scan
    assert "--pkg-types os,library" in scan
    assert "--severity CRITICAL,HIGH" in scan
    assert "--exit-code 1" in scan
    assert "--ignore-unfixed" in scan
    assert "--output trivy-hive-image.txt" in scan
    assert "trivy-hive-image.txt" in evidence
    assert "if: always()" in evidence


def test_all_workflow_actions_are_immutably_pinned() -> None:
    for path in (WATCHER_PATH, CI_PATH):
        uses = re.findall(r"^\s*- uses:\s*([^\s#]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert uses
        for action in uses:
            assert re.search(r"@[0-9a-f]{40}$", action), action

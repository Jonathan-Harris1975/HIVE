"""Hosted proof of isolated fixtures; no production file is edited or installed."""

import json
import subprocess
from pathlib import Path

root = Path("ci-fixtures/negative-controls")
out = Path("negative-control-evidence")
out.mkdir(exist_ok=True)
results = {}


def run(name, command):
    process = subprocess.run(command, text=True, capture_output=True, timeout=360)
    (out / (name + ".stdout")).write_text(process.stdout)
    (out / (name + ".stderr")).write_text(process.stderr)
    results[name] = {"exit": process.returncode}
    return process


try:
    result = run("actionlint", [".ci-tools/bin/actionlint", "-oneline", str(root / "incorrect.yml")])
    assert result.returncode != 0 and "nonexistent" in result.stdout + result.stderr
    results["actionlint"]["expected_defect_detected"] = True
    result = run("zizmor", [".ci-tools/bin/zizmor", "--offline", "--no-config", "--format", "json", str(root / "unsafe.yml")])
    findings = json.loads(result.stdout)
    assert result.returncode != 0 and any(f["ident"] == "template-injection" and not f["ignored"] for f in findings)
    results["zizmor"]["expected_defect_detected"] = True
    result = run("renovate", ["npx", "--yes", "--package", "renovate@44.132.5", "--", "renovate-config-validator", "--strict", str(root / "renovate.json")])
    assert result.returncode != 0 and "Configuration option `automerge` should be boolean" in result.stdout + result.stderr
    results["renovate"]["expected_defect_detected"] = True
finally:
    (out / "results.json").write_text(json.dumps(results, indent=2) + "\n")

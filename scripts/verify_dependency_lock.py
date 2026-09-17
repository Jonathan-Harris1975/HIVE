"""Verify HIVE's direct pins and compiled production lock stay in sync."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?==([^;\s]+)$")


def normalise_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def read_exact_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = PIN_RE.fullmatch(line)
        if not match:
            raise AssertionError(f"{path.name}:{line_number}: dependency is not an exact pin: {line}")
        name = normalise_name(match.group(1))
        if name in pins:
            raise AssertionError(f"{path.name} contains duplicate package: {name}")
        pins[name] = match.group(2)
    return pins


def verify_direct_pins() -> None:
    direct = read_exact_requirements(ROOT / "requirements.in")
    compiled = read_exact_requirements(ROOT / "requirements.txt")
    missing = {name: version for name, version in direct.items() if compiled.get(name) != version}
    if missing:
        details = ", ".join(f"{name}=={version}" for name, version in sorted(missing.items()))
        raise AssertionError(f"requirements.txt does not match direct production pins: {details}")


def verify_compiled_lock() -> None:
    committed = read_exact_requirements(ROOT / "requirements.txt")
    with tempfile.TemporaryDirectory(prefix="hive-lock-") as tmp:
        generated_path = Path(tmp) / "requirements.txt"
        # Seed pip-compile with the committed lock so this verifies compatibility
        # instead of needlessly upgrading unrelated transitive packages.
        shutil.copy2(ROOT / "requirements.txt", generated_path)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "piptools",
                "compile",
                "--quiet",
                "--strip-extras",
                "--output-file",
                str(generated_path),
                str(ROOT / "requirements.in"),
            ],
            cwd=ROOT,
            check=True,
        )
        generated = read_exact_requirements(generated_path)
    if generated != committed:
        changed = sorted(set(generated) | set(committed))
        differences = [
            f"{name}: committed={committed.get(name)!r}, compiled={generated.get(name)!r}"
            for name in changed
            if committed.get(name) != generated.get(name)
        ]
        raise AssertionError("compiled production lock differs:\n" + "\n".join(differences))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile", action="store_true", help="re-run pip-compile and compare every production pin")
    args = parser.parse_args()
    verify_direct_pins()
    if args.compile:
        verify_compiled_lock()
    print("HIVE dependency lock verification passed")


if __name__ == "__main__":
    main()

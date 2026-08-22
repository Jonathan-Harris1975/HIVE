#!/usr/bin/env python3
"""Repository-local secret guard.

Designed for CI and pre-deploy checks. It deliberately allows secret-manager
references such as ``{{ secret.NAME }}``, shell references, empty example
values and clearly synthetic local/test values. It fails on private key
material, recognised provider token formats and literal values assigned to
secret-bearing environment/config keys.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    "", ".env", ".example", ".txt", ".md", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".py", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".sh",
}
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "playwright-report", "test-results",
}
SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_?KEY|ACCESS_?KEY|"
    r"SECRET_?ACCESS_?KEY|CLIENT_?SECRET|BEARER)(?:$|_)",
    re.IGNORECASE,
)
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
KNOWN_TOKEN_PATTERNS = [
    ("OpenAI/OpenRouter-style API key", re.compile(r"\bsk-(?:or-)?[A-Za-z0-9_-]{20,}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
]
PLACEHOLDER_REF = re.compile(
    r"^(?:\{\{\s*secret\.[^}]+\}\}|\$\{[^}]+\}|<[^>]+>|"
    r"(?:change|replace)(?:[-_ ]?me|[-_ ].*)?|your[-_ ][A-Za-z0-9_-]+|"
    r"(?:example|dummy|fake|test|ci|local)(?:[-_ ].*)?|\.{3})$",
    re.IGNORECASE,
)


def tracked_files(root: Path) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if proc.stdout:
            return [root / p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in root.rglob("*") if p.is_file()]


def is_text_candidate(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if path.stat().st_size > 2_000_000:
        return False
    suffix = path.suffix.lower()
    name = path.name.lower()
    return suffix in TEXT_SUFFIXES or ".env" in name or "env" in name


def strip_inline_comment(value: str) -> str:
    value = value.strip().strip('"\'')
    if value.startswith("#"):
        return ""
    # .env comments are recognised when # is preceded by whitespace.
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"\'')
    return value


def is_safe_literal(value: str) -> bool:
    if not value:
        return True
    if PLACEHOLDER_REF.fullmatch(value) or value.startswith("... "):
        return True
    lowered = value.lower()
    if any(marker in lowered for marker in ("example.invalid", "localhost", "127.0.0.1")):
        return True
    if any(marker in lowered for marker in ("example-", "-example", "test-", "-test", "dummy", "fake-", "local-only", "ci-")):
        return True
    return False


def scan_file(path: Path, root: Path) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings
    rel = path.relative_to(root).as_posix()
    for line_no, line in enumerate(text.splitlines(), 1):
        if PRIVATE_KEY.search(line):
            findings.append(f"{rel}:{line_no}: private key material")

        # Known token formats are strong signals. Synthetic/test/example lines are allowed.
        known_fixture = "AKIAABCDEFGHIJKLMNOP" in line
        if not known_fixture and not any(word in line.lower() for word in ("test", "example", "dummy", "fake", "placeholder")):
            for label, pattern in KNOWN_TOKEN_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{line_no}: {label}")

        env_like = (".env" in path.name.lower() or path.suffix.lower() in {".txt", ".md", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf"})
        if not env_like:
            continue
        assignment = ENV_ASSIGNMENT.match(line)
        if not assignment:
            continue
        key, raw_value = assignment.groups()
        if not SENSITIVE_KEY.search(key):
            continue
        value = strip_inline_comment(raw_value)
        if is_safe_literal(value):
            continue
        # Short literals are commonly non-secret modes/identifiers. A production
        # credential should be long enough to trigger this guard.
        if len(value) >= 16:
            findings.append(f"{rel}:{line_no}: literal value assigned to {key}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if committed source appears to contain literal secrets.")
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    findings: list[str] = []
    for path in tracked_files(root):
        if is_text_candidate(path, root):
            findings.extend(scan_file(path, root))
    if findings:
        print("Secret scan failed:", file=sys.stderr)
        for finding in sorted(set(findings)):
            print(f"  - {finding}", file=sys.stderr)
        print("Use a secret-manager reference, an empty example value, or a clearly synthetic test value.", file=sys.stderr)
        return 1
    print("Secret scan passed: no committed literal credentials detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

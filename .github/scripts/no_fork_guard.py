#!/usr/bin/env python3
"""Block direct GitHub fork creation in repository automation/source code.

The owner-approval rule is in AGENTS.md. This guard recognises common executable
fork creation patterns. It is defence in depth, not a GitHub account permission.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_EXTENSIONS = frozenset({'.py', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.sh', '.bash', '.go', '.rb', '.ps1', '.yml', '.yaml'})
IGNORED_DIRECTORIES = frozenset({'node_modules', '.venv', 'venv', 'vendor', 'coverage', 'dist', 'build', 'test', 'tests', '__fixtures__', 'fixtures', 'docs'})
IGNORED_FILE = '.github/scripts/no_fork_guard.py'
SIGNATURES = (
    ('GitHub CLI fork command', re.compile(r'\bgh\s+repo\s+fork\b', re.I)),
    ('hub CLI fork command', re.compile(r'\bhub\s+fork\b', re.I)),
    ('GitHub SDK fork method', re.compile(r'\b(?:createFork|create_fork|fork_repository)\s*\(', re.I)),
    ('GitHub REST fork POST', re.compile(r'\bPOST\b[\s\S]{0,350}/forks\b|/forks\b[\s\S]{0,350}\bPOST\b', re.I)),
    ('GitHub REST fork POST helper', re.compile(r'\b(?:requests\.post|\.post|request\.post)\s*\([^)]{0,350}/forks\b', re.I)),
)


def find_signatures(content: str) -> list[tuple[int, str]]:
    hits = []
    for name, pattern in SIGNATURES:
        for match in pattern.finditer(content):
            hits.append((content.count('\n', 0, match.start()) + 1, name))
    return sorted(set(hits))


def self_test() -> None:
    blocked = (
        'gh repo fork Jonathan-Harris1975/AIMS',
        'hub fork',
        'await octokit.rest.repos.createFork({owner: "x", repo: "y"})',
        'requests.post("https://api.github.com/repos/x/y/forks")',
        'request("POST", "/repos/x/y/forks")',
    )
    allowed = (
        'gh pr create --head fix/update --base main',
        'git checkout -b fix/update',
        'fork in the road',
        'request("GET", "/repos/x/y/forks")',
    )
    for item in blocked:
        if not find_signatures(item):
            raise AssertionError(f'Fork creation unexpectedly allowed: {item}')
    for item in allowed:
        if find_signatures(item):
            raise AssertionError(f'Harmless operation unexpectedly blocked: {item}')
    print('No-fork guard self-test passed.')


def tracked_source_files() -> list[Path]:
    try:
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError('Cannot enumerate source files; refusing to skip fork policy check') from exc
    paths = []
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        relative = Path(entry.decode('utf-8', 'surrogateescape'))
        if relative.as_posix() == IGNORED_FILE or relative.suffix not in SOURCE_EXTENSIONS:
            continue
        if set(relative.parts) & IGNORED_DIRECTORIES:
            continue
        if relative.name.startswith('test_') or relative.name.endswith(('_test.py', '.test.js', '.test.ts', '.spec.js', '.spec.ts')):
            continue
        file = ROOT / relative
        if file.is_file():
            paths.append(file)
    return paths


def main() -> int:
    if sys.argv[1:] == ['--self-test']:
        self_test()
        return 0
    if sys.argv[1:]:
        raise SystemExit('Usage: no_fork_guard.py [--self-test]')
    found = 0
    for file in tracked_source_files():
        try:
            source = file.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise RuntimeError(f'Could not inspect {file}; refusing to skip policy check') from exc
        for line, name in find_signatures(source):
            relative = file.relative_to(ROOT).as_posix()
            print(f'::error file={relative},line={line}::{name} is prohibited without explicit owner approval')
            found += 1
    if found:
        print(f'No-fork policy FAILED: {found} fork-creation signature(s) found. Use HUMAN_HOLD.')
        return 1
    print('No-fork policy passed: no direct fork-creation code detected.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

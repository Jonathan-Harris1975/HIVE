from __future__ import annotations

import ast
import py_compile
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services import repo_hygiene
from app.services.repository_manager import RepositoryManagerError, get_repository, reindex_repository

# Phase 7 - Repository QA.
#
# IMPORTANT DESIGN NOTE (read before trusting this pipeline): Repository QA
# deliberately performs only *static* analysis over an uploaded repository's
# working copy — it never installs dependencies, runs `npm`/`pip install`,
# or executes the repository's own build/test commands. Uploaded ZIPs are
# untrusted input; actually executing them would be an arbitrary-code-
# execution risk inside HIVE's own backend process. Real dynamic build/lint/
# type-check/test execution belongs in an isolated, sandboxed CI runner if
# HIVE ever adds one — this module is not that, and callers should not treat
# a clean report here as equivalent to a real CI pass.
#
# What each check actually does:
#   build_verification   - py_compile every .py file (a real syntax/compile
#                           check); presence-only detection of JS/TS build
#                           tooling (package.json build script) since JS
#                           isn't executed.
#   lint                 - lightweight heuristics (trailing whitespace,
#                           very long lines, mixed tabs/spaces) — not a
#                           substitute for ruff/eslint.
#   type_checking         - heuristic type-hint coverage ratio on Python
#                           files — not a substitute for mypy/pyright.
#   dependency_validation - reuses the Phase 1 manifest's dependency scan.
#   import_validation     - parses Python imports via `ast` and flags local
#                           (relative or same-package) imports that don't
#                           resolve to a file in the repository.
#   dead_code_detection   - reuses the existing `repo_hygiene_report`
#                           (duplicate/orphan/generated-artifact scan).
#   security_scanning     - regex heuristics for obviously-committed
#                           secrets (AWS keys, private key headers, etc.) —
#                           not a substitute for a real secret scanner.
#   regression_testing    - presence-count of test files/directories; tests
#                           are never executed.
#   patch_verification    - compares the current manifest fingerprint
#                           against the repository's registered fingerprint
#                           to confirm the working copy hasn't silently
#                           drifted since Phase 1 registration.
#   architecture_validation - flags conflicting/mixed build-system markers
#                           (e.g. both requirements.txt and pyproject.toml
#                           with a poetry section) as a heuristic smell.

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("generic_api_key_assignment", re.compile(r"(?i)(api[_-]?key|secret)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]")),
)

_TEST_FILE_PATTERN = re.compile(r"(^|/)(tests?/|test_[^/]+\.py$|[^/]+\.test\.[jt]sx?$|[^/]+_test\.py$)")


@dataclass(frozen=True)
class QaCheckResult:
    name: str
    status: str  # "ok" | "warning" | "skipped"
    summary: str
    details: dict[str, Any]


@dataclass(frozen=True)
class QaReport:
    repository_id: str
    checks: list[QaCheckResult]
    warning_count: int
    score: float

    def public_payload(self) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "score": self.score,
            "warning_count": self.warning_count,
            "checks": [asdict(check) for check in self.checks],
        }


def _iter_source_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    ignored = repo_hygiene.DEFAULT_IGNORED_DIRS
    results = []
    for candidate in root.rglob("*"):
        if candidate.is_dir():
            continue
        if any(part in ignored for part in candidate.relative_to(root).parts):
            continue
        if candidate.suffix in suffixes:
            results.append(candidate)
    return results


def _check_build_verification(root: Path) -> QaCheckResult:
    python_files = _iter_source_files(root, (".py",))
    failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory() as cache_dir:
        for path in python_files:
            try:
                # quiet=2 tells py_compile to swallow compile errors *instead of*
                # raising them, regardless of doraise — so quiet=2 here silently
                # defeated this whole check (every file "passed" even with a
                # syntax error). quiet=1 still suppresses the printed traceback
                # but leaves doraise honored, which is what this check needs.
                py_compile.compile(
                    str(path), cfile=str(Path(cache_dir) / "out.pyc"), doraise=True, quiet=1
                )
            except py_compile.PyCompileError as error:
                failures.append({"path": str(path.relative_to(root)), "error": str(error.exc_value)})

    has_package_json_build = (root / "package.json").exists()
    status = "warning" if failures else "ok"
    return QaCheckResult(
        name="build_verification",
        status=status,
        summary=f"{len(python_files)} Python files compiled, {len(failures)} failed"
        + (" (JS/TS build not executed — presence-only)" if has_package_json_build else ""),
        details={"failures": failures, "python_file_count": len(python_files)},
    )


def _check_lint(root: Path) -> QaCheckResult:
    issues: list[dict[str, Any]] = []
    for path in _iter_source_files(root, (".py", ".js", ".ts", ".tsx", ".jsx")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if line != line.rstrip():
                issues.append({"path": str(path.relative_to(root)), "line": lineno, "issue": "trailing_whitespace"})
            if len(line) > 200:
                issues.append({"path": str(path.relative_to(root)), "line": lineno, "issue": "line_too_long"})
            if "\t" in line and "    " in line:
                issues.append({"path": str(path.relative_to(root)), "line": lineno, "issue": "mixed_tabs_spaces"})
    return QaCheckResult(
        name="lint",
        status="warning" if issues else "ok",
        summary=f"{len(issues)} heuristic lint issues (not a substitute for ruff/eslint)",
        details={"issues": issues[:200], "issue_count": len(issues)},
    )


def _check_type_checking(root: Path) -> QaCheckResult:
    python_files = _iter_source_files(root, (".py",))
    total_functions = 0
    annotated_functions = 0
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total_functions += 1
                if node.returns is not None or any(arg.annotation is not None for arg in node.args.args):
                    annotated_functions += 1
    ratio = (annotated_functions / total_functions) if total_functions else 1.0
    return QaCheckResult(
        name="type_checking",
        status="ok" if ratio >= 0.5 or total_functions == 0 else "warning",
        summary=f"{annotated_functions}/{total_functions} functions have some type annotation (heuristic, not mypy)",
        details={"annotation_ratio": round(ratio, 3), "total_functions": total_functions},
    )


def _check_dependency_validation(manifest_dependencies: list[dict[str, Any]]) -> QaCheckResult:
    unpinned = [
        dep["manifest_path"]
        for dep in manifest_dependencies
        if dep.get("ecosystem") == "pip" and not dep.get("declared")
    ]
    return QaCheckResult(
        name="dependency_validation",
        status="ok",
        summary=f"{len(manifest_dependencies)} dependency manifest(s) detected",
        details={"manifests": manifest_dependencies, "unparsed_pip_manifests": unpinned},
    )


def _check_import_validation(root: Path) -> QaCheckResult:
    python_files = _iter_source_files(root, (".py",))
    existing_modules = {path.relative_to(root).with_suffix("").as_posix().replace("/", ".") for path in python_files}
    broken: list[dict[str, str]] = []
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                # Relative import — best-effort existence check only for
                # depth-1 relative imports to avoid false positives on
                # complex package layouts.
                if node.module and node.level == 1:
                    package = path.parent.relative_to(root).as_posix().replace("/", ".")
                    candidate = f"{package}.{node.module}" if package else node.module
                    if candidate not in existing_modules and not any(
                        m.startswith(candidate) for m in existing_modules
                    ):
                        broken.append({"path": str(path.relative_to(root)), "module": node.module})
    return QaCheckResult(
        name="import_validation",
        status="warning" if broken else "ok",
        summary=f"{len(broken)} relative import(s) could not be resolved locally (best-effort)",
        details={"unresolved": broken},
    )


def _check_dead_code(root: Path) -> QaCheckResult:
    report = repo_hygiene.repo_hygiene_report(repo_root=root, include_hashes=True)
    total_flagged = (
        report["duplicate_content_group_count"]
        + report["orphan_candidate_count"]
        + report["generated_artifact_count"]
    )
    return QaCheckResult(
        name="dead_code_detection",
        status="warning" if total_flagged else "ok",
        summary=f"{total_flagged} duplicate/orphan/generated-artifact item(s) flagged",
        details={
            "duplicate_content_group_count": report["duplicate_content_group_count"],
            "orphan_candidate_count": report["orphan_candidate_count"],
            "generated_artifact_count": report["generated_artifact_count"],
        },
    )


def _check_security_scan(root: Path) -> QaCheckResult:
    findings: list[dict[str, str]] = []
    for path in _iter_source_files(root, (".py", ".js", ".ts", ".env", ".json", ".yaml", ".yml", ".txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({"path": str(path.relative_to(root)), "pattern": label})
    return QaCheckResult(
        name="security_scanning",
        status="warning" if findings else "ok",
        summary=f"{len(findings)} possible secret pattern match(es) (heuristic, review before trusting)",
        details={"findings": findings},
    )


def _check_regression_testing(root: Path) -> QaCheckResult:
    all_files = [p for p in root.rglob("*") if p.is_file()]
    test_files = [p for p in all_files if _TEST_FILE_PATTERN.search(p.relative_to(root).as_posix())]
    status = "ok" if test_files else "warning"
    return QaCheckResult(
        name="regression_testing",
        status=status,
        summary=f"{len(test_files)} test file(s) detected (not executed)",
        details={"test_file_count": len(test_files)},
    )


def _check_patch_verification(repository_id: str, current_fingerprint: str, registered_fingerprint: str) -> QaCheckResult:
    drifted = current_fingerprint != registered_fingerprint
    return QaCheckResult(
        name="patch_verification",
        status="warning" if drifted else "ok",
        summary="Working copy fingerprint drifted since registration" if drifted else "Fingerprint matches registration",
        details={"current_fingerprint": current_fingerprint, "registered_fingerprint": registered_fingerprint},
    )


def _check_architecture_validation(root: Path) -> QaCheckResult:
    markers = {
        "requirements.txt": (root / "requirements.txt").exists(),
        "pyproject.toml": (root / "pyproject.toml").exists(),
        "package.json": (root / "package.json").exists(),
        "go.mod": (root / "go.mod").exists(),
        "Cargo.toml": (root / "Cargo.toml").exists(),
    }
    python_marker_count = sum(1 for key in ("requirements.txt", "pyproject.toml") if markers[key])
    mixed = python_marker_count > 1
    return QaCheckResult(
        name="architecture_validation",
        status="warning" if mixed else "ok",
        summary="Multiple competing Python dependency manifests detected" if mixed else "No conflicting build-system markers detected",
        details={"markers": markers},
    )


def run_repository_qa(repository_id: str) -> QaReport:
    record = get_repository(repository_id)
    if record is None:
        raise RepositoryManagerError(f"Unknown repository_id: {repository_id}")

    registered_fingerprint = record.manifest.fingerprint
    current_manifest = reindex_repository(repository_id)
    root = record.workdir

    checks = [
        _check_build_verification(root),
        _check_lint(root),
        _check_type_checking(root),
        _check_dependency_validation(
            [asdict(dep) for dep in current_manifest.dependencies]
        ),
        _check_import_validation(root),
        _check_dead_code(root),
        _check_security_scan(root),
        _check_regression_testing(root),
        _check_patch_verification(repository_id, current_manifest.fingerprint, registered_fingerprint),
        _check_architecture_validation(root),
    ]
    warning_count = sum(1 for check in checks if check.status == "warning")
    score = max(0.0, 1.0 - (warning_count / len(checks)))

    return QaReport(repository_id=repository_id, checks=checks, warning_count=warning_count, score=round(score, 3))

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import Settings
from app.services import repository_manager as rm
from app.services.repository_qa import run_repository_qa


def _build_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolated_registry():
    rm._REGISTRY.clear()
    yield
    for repository_id in list(rm._REGISTRY.keys()):
        rm.cleanup_repository(repository_id)


@pytest.fixture
def settings(tmp_path):
    return Settings(REPOSITORY_TEMP_DIR=str(tmp_path))


def test_run_repository_qa_returns_all_ten_checks(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "def hello() -> str:\n    return 'hi'\n", "README.md": "# demo\n"}),
        settings=settings,
        source_filename="demo.zip",
    )

    report = run_repository_qa(manifest.repository_id)

    assert len(report.checks) == 10
    check_names = {check.name for check in report.checks}
    assert check_names == {
        "build_verification",
        "lint",
        "type_checking",
        "dependency_validation",
        "import_validation",
        "dead_code_detection",
        "security_scanning",
        "regression_testing",
        "patch_verification",
        "architecture_validation",
    }
    assert 0.0 <= report.score <= 1.0


def test_build_verification_flags_python_syntax_errors(settings):
    manifest = rm.register_repository(
        _build_zip({"broken.py": "def broken(:\n    pass\n"}), settings=settings, source_filename="demo.zip"
    )

    report = run_repository_qa(manifest.repository_id)

    build_check = next(c for c in report.checks if c.name == "build_verification")
    assert build_check.status == "warning"
    assert build_check.details["failures"]


def test_security_scanning_flags_committed_secrets(settings):
    manifest = rm.register_repository(
        _build_zip({"config.py": "AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\n"}),
        settings=settings,
        source_filename="demo.zip",
    )

    report = run_repository_qa(manifest.repository_id)

    security_check = next(c for c in report.checks if c.name == "security_scanning")
    assert security_check.status == "warning"
    assert security_check.details["findings"]


def test_regression_testing_detects_test_files(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "x = 1\n", "tests/test_main.py": "def test_x():\n    assert True\n"}),
        settings=settings,
        source_filename="demo.zip",
    )

    report = run_repository_qa(manifest.repository_id)

    regression_check = next(c for c in report.checks if c.name == "regression_testing")
    assert regression_check.status == "ok"
    assert regression_check.details["test_file_count"] == 1


def test_patch_verification_ok_when_no_drift(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "x = 1\n"}), settings=settings, source_filename="demo.zip"
    )

    report = run_repository_qa(manifest.repository_id)

    patch_check = next(c for c in report.checks if c.name == "patch_verification")
    assert patch_check.status == "ok"


def test_run_repository_qa_raises_for_unknown_repository(settings):
    from app.services.repository_manager import RepositoryManagerError

    with pytest.raises(RepositoryManagerError):
        run_repository_qa("does-not-exist")

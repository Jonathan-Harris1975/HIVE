from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import Settings
from app.services import repository_manager as rm
from app.services.repository_profile import build_repository_memory_profile


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


def test_repository_profile_populates_all_automatic_scalar_fields(tmp_path):
    settings = Settings(REPOSITORY_TEMP_DIR=str(tmp_path))
    archive = _build_zip(
        {
            "HIVE-UI-main/package.json": '{"dependencies":{"react":"19"},"scripts":{"build":"vite build","test":"vitest run"}}',
            "HIVE-UI-main/src/App.tsx": "export const App = () => null\n",
            "HIVE-UI-main/eslint.config.js": "export default []\n",
            "HIVE-UI-main/wrangler.toml": 'name = "hive-ui"\n',
            "HIVE-UI-main/.env.example": "API_URL=\nSECRET_VALUE=\n",
            "HIVE-UI-main/.github/workflows/ci.yml": "name: CI\n",
            "HIVE-UI-main/src/App.test.tsx": "test('x', () => {})\n",
        }
    )
    manifest = rm.register_repository(archive, settings=settings, source_filename="HIVE-UI-main.zip")
    record = rm.get_repository(manifest.repository_id)
    assert record is not None

    profile = build_repository_memory_profile(record)

    assert set(profile) == {
        "architecture_summary",
        "coding_standards",
        "build_profile",
        "deployment_profile",
        "environment_schema",
    }
    assert "React" in profile["architecture_summary"]["architecture_signals"]
    assert profile["architecture_summary"]["top_level_directories"] == [".github", "src"]
    assert profile["build_profile"]["package_scripts"]["build"] == "vite build"
    assert "Cloudflare Workers" in profile["deployment_profile"]["targets"]
    assert profile["environment_schema"]["variables"] == ["API_URL", "SECRET_VALUE"]
    assert profile["coding_standards"]["test_file_count"] == 1

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def test_skill_from_file_route_is_removed(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    monkeypatch.setitem(app.dependency_overrides, get_settings, get_settings)

    response = TestClient(app).post(
        "/v1/skills/from-file",
        json={
            "title": "Remote descriptor",
            "object_key": "skills/example.json",
            "source_lane": "hive_skills",
        },
    )

    assert response.status_code == 404

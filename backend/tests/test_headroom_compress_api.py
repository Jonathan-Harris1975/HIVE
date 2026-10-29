from dataclasses import replace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.headroom_compress import router, _capacity
from app.core.config import Settings, get_settings
from app.services.headroom_optimizer import HeadroomOutcome


def client():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_settings] = lambda: Settings(aims_api_key="test-only-service", headroom_enabled=True)
    return TestClient(app)


BODY = {"model": "openai/test", "messages": [
    {"role": "system", "content": "Protected system"},
    {"role": "user", "content": "Repeated context " * 100, "name": "preserved-metadata"},
    {"role": "user", "content": "Protected latest"},
]}
AUTH = {"Authorization": "Bearer test-only-service"}


def outcome():
    return HeadroomOutcome(messages=[{"role": row["role"], "content": "Reduced context" if i == 1 else row["content"]}
                                      for i, row in enumerate(BODY["messages"])],
                           attempted=True, compressed=True, tokens_before=1000, tokens_after=500, tokens_saved=500)


def test_auth_is_required_before_compression():
    with client() as c, patch("app.api.headroom_compress.HeadroomOptimizer.optimise") as run:
        assert c.post("/v1/compress", json=BODY).status_code == 401
        assert c.post("/v1/compress", json=BODY, headers={"Authorization": "Bearer wrong"}).status_code == 403
        run.assert_not_called()


def test_compression_preserves_metadata_and_protected_instructions_without_inference():
    with client() as c, patch("app.api.headroom_compress.HeadroomOptimizer.optimise", return_value=outcome()) as run:
        r = c.post("/v1/compress", json=BODY, headers=AUTH)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        data = r.json()
        assert data["tokens_saved"] == 500
        assert data["messages"][0] == BODY["messages"][0]
        assert data["messages"][-1] == BODY["messages"][-1]
        assert data["messages"][1]["name"] == "preserved-metadata"
        run.assert_called_once()


def test_protected_instruction_changes_are_rejected():
    unsafe = outcome()
    unsafe.messages[-1]["content"] = "changed"
    with client() as c, patch("app.api.headroom_compress.HeadroomOptimizer.optimise", return_value=unsafe):
        assert c.post("/v1/compress", json=BODY, headers=AUTH).status_code == 503


def test_failure_and_no_savings_never_replace_original_messages():
    with client() as c:
        for bad in [replace(outcome(), failed=True), replace(outcome(), skipped_reason="disabled")]:
            with patch("app.api.headroom_compress.HeadroomOptimizer.optimise", return_value=bad):
                assert c.post("/v1/compress", json=BODY, headers=AUTH).status_code == 503
        with patch("app.api.headroom_compress.HeadroomOptimizer.optimise", return_value=replace(outcome(), compressed=False, tokens_saved=0)):
            assert c.post("/v1/compress", json=BODY, headers=AUTH).json()["messages"] == BODY["messages"]


def test_context_and_shape_limits_apply_before_compression():
    with client() as c, patch("app.api.headroom_compress.HeadroomOptimizer.optimise") as run:
        assert c.post("/v1/compress", json={"messages": [], "model": "test"}, headers=AUTH).status_code == 422
        assert c.post("/v1/compress", json={"messages": [{"role": "user", "content": "x" * 262145}], "model": "test"}, headers=AUTH).status_code == 413
        assert c.post("/v1/compress", content=b"x" * 1500001, headers=AUTH).status_code == 413
        run.assert_not_called()


def test_capacity_is_bounded():
    assert _capacity.acquire(blocking=False)
    try:
        with client() as c, patch("app.api.headroom_compress.HeadroomOptimizer.optimise") as run:
            assert c.post("/v1/compress", json=BODY, headers=AUTH).status_code == 503
            run.assert_not_called()
    finally:
        _capacity.release()

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router
from app.core.config import get_settings


class SpendRouteTests(unittest.TestCase):
    def test_private_report_requires_bearer_auth_and_is_not_cached(self):
        app = FastAPI()
        app.include_router(router, prefix="/v1")
        app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
            admin_bearer_token="test-only-admin", is_dev=False, r2_bucket_meta_system="metasystem")
        with TestClient(app) as client:
            with patch("app.api.system.R2Storage", return_value=object()), patch(
                    "app.api.system.read_spend_report", return_value={"ok": True, "available": False, "report": None}) as read:
                self.assertEqual(client.get("/v1/system/llm-spend").status_code, 401)
                read.assert_not_called()
                result = client.get("/v1/system/llm-spend", headers={"Authorization": "Bearer test-only-admin"})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers["cache-control"], "no-store")
                self.assertFalse(result.json()["available"])
                self.assertEqual(read.call_count, 1)
                invalid = client.get("/v1/system/llm-spend?date=../secrets", headers={"Authorization": "Bearer test-only-admin"})
                self.assertEqual(invalid.status_code, 422)

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings


class VectorizeClient:
    """Cloudflare Vectorize adapter skeleton.

    The API wrapper is deliberately thin so the app can swap Vectorize for pgvector later if needed.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(settings.cf_account_id and settings.cf_api_token and settings.cf_vectorize_index)

    @property
    def base_url(self) -> str:
        return f"https://api.cloudflare.com/client/v4/accounts/{self.settings.cf_account_id}/vectorize/v2/indexes/{self.settings.cf_vectorize_index}"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.cf_api_token}",
            "Content-Type": "application/json",
        }

    async def upsert_vectors(self, vectors: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "Vectorize not configured"}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/upsert", headers=self.headers, json={"vectors": vectors})
            response.raise_for_status()
            return response.json()

    async def query(self, vector: list[float], top_k: int = 8) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "Vectorize not configured", "matches": []}
        payload = {"vector": vector, "topK": top_k, "returnMetadata": True}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/query", headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()

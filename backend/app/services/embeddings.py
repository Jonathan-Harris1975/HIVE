from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.core.redaction import redact_payload, redact_text

logger = logging.getLogger("uvicorn.error.hive.embeddings")


class CloudflareEmbeddingsClient:
    """Small Cloudflare Workers AI embeddings adapter.

    The adapter is intentionally defensive: response shapes can vary slightly
    between Workers AI models, so ``_extract_vectors`` accepts the common
    ``result.data`` and ``result`` forms and returns a clear diagnostic instead
    of raising a vague upstream error.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self.enabled = bool(
            settings.embeddings_enabled
            and settings.embeddings_provider.lower() == "cloudflare"
            and settings.embeddings_account_id
            and settings.embeddings_api_token
            and settings.embeddings_model
        )

    @property
    def endpoint(self) -> str:
        model = self.settings.embeddings_model.lstrip("/")
        return f"https://api.cloudflare.com/client/v4/accounts/{self.settings.embeddings_account_id}/ai/run/{model}"

    @property
    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": self.settings.embeddings_provider,
            "model": self.settings.embeddings_model,
            "dimensions": self.settings.embeddings_dimensions,
            "account_configured": bool(self.settings.embeddings_account_id),
            "api_token_configured": bool(self.settings.embeddings_api_token),
            "timeout_seconds": self.settings.embeddings_timeout_seconds,
            "max_batch_size": self.settings.embeddings_max_batch_size,
        }

    async def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": self.enabled, **self.safe_config}
        if not self.enabled:
            payload["reason"] = "Embeddings disabled or not fully configured."
        return payload

    async def embed_texts(self, texts: list[str]) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "error": "Embeddings disabled or not configured."}
        if not texts:
            return {"ok": False, "enabled": True, "error": "No texts supplied for embedding."}

        clean_texts = [text if isinstance(text, str) else str(text) for text in texts]
        payload = {"text": clean_texts}
        headers = {"Authorization": f"Bearer {self.settings.embeddings_api_token}", "Content-Type": "application/json"}
        timeout = max(1.0, float(self.settings.embeddings_timeout_seconds))
        secret = self.settings.embeddings_api_token

        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
            raw, json_ok = _safe_json(response)
            safe_raw = redact_payload(raw, secret)
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "enabled": True,
                    "status_code": response.status_code,
                    "error": redact_text(_cloudflare_error_text(safe_raw) or response.text, secret),
                    "raw": safe_raw,
                }
            if not json_ok:
                return {
                    "ok": False,
                    "enabled": True,
                    "status_code": response.status_code,
                    "error": "Embeddings provider returned malformed JSON.",
                    "raw": safe_raw,
                }
            if not isinstance(raw, dict):
                return {
                    "ok": False,
                    "enabled": True,
                    "status_code": response.status_code,
                    "error": f"Unexpected embeddings response type: {type(raw).__name__}.",
                    "raw": safe_raw,
                }
            vectors = _extract_vectors(raw)
            if len(vectors) != len(clean_texts):
                return {
                    "ok": False,
                    "enabled": True,
                    "status_code": response.status_code,
                    "error": f"Embedding count mismatch: expected {len(clean_texts)}, got {len(vectors)}.",
                    "raw": safe_raw,
                }
            return {
                "ok": True,
                "enabled": True,
                "status_code": response.status_code,
                "count": len(vectors),
                "dimensions": len(vectors[0]) if vectors else 0,
                "vectors": vectors,
                "raw": safe_raw,
            }
        except Exception as exc:  # provider/network exceptions degrade the optional integration
            safe_error = redact_text(exc, secret)
            logger.warning(
                "Cloudflare embeddings request failed error_type=%s error=%s",
                type(exc).__name__,
                safe_error,
            )
            return {"ok": False, "enabled": True, "error": safe_error, "type": type(exc).__name__}


def _safe_json(response: httpx.Response) -> tuple[Any, bool]:
    try:
        return response.json(), True
    except (TypeError, ValueError):
        return {"text": response.text}, False


def _cloudflare_error_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages = [str(item.get("message") or item) for item in errors if isinstance(item, dict)]
        return "; ".join(messages) if messages else str(errors)
    return None


def _extract_vectors(payload: Any) -> list[list[float]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result", payload)

    candidates: Any = None
    if isinstance(result, dict):
        candidates = result.get("data") or result.get("embeddings") or result.get("vectors")
        if candidates is None and "shape" in result and "data" in result:
            candidates = result.get("data")
    else:
        candidates = result

    if isinstance(candidates, list):
        if candidates and all(isinstance(value, (int, float)) for value in candidates):
            return [[float(value) for value in candidates]]
        vectors: list[list[float]] = []
        for item in candidates:
            if isinstance(item, dict):
                vector = item.get("embedding") or item.get("vector") or item.get("values")
            else:
                vector = item
            if isinstance(vector, list) and all(isinstance(value, (int, float)) for value in vector):
                vectors.append([float(value) for value in vector])
        return vectors
    return []

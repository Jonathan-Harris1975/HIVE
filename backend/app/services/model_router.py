from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.config import Settings


class TaskType(StrEnum):
    GENERAL = "general"
    SUMMARY = "summary"
    CODE = "code"
    AUDIT = "audit"
    FILE_TRIAGE = "file_triage"
    PREMIUM = "premium"


class Mode(StrEnum):
    AUTO = "auto"
    BRAND = "brand"
    GENERAL = "general"
    CODE = "code"
    FILE_ANALYSIS = "file_analysis"
    AUDIT = "audit"


class ModelRouter:
    """Small, explicit model-policy layer.

    Rule one: an explicit request.model wins. The router only chooses a model when the
    request leaves model blank. This matters for testing free models and for future UI
    model switching.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify_task(self, user_message: str, requested_mode: Mode = Mode.AUTO) -> TaskType:
        text = user_message.lower()
        if requested_mode == Mode.CODE or any(
            word in text for word in ["repo", "python", "javascript", "bug", "traceback", "ci error"]
        ):
            return TaskType.CODE
        if requested_mode in {Mode.AUDIT, Mode.BRAND} or any(
            word in text for word in ["aims", "rams", "audit", "quarantine", "koyeb", "gate"]
        ):
            return TaskType.AUDIT
        if requested_mode == Mode.FILE_ANALYSIS or any(
            word in text for word in ["upload", "zip", "spreadsheet", "pdf", "file"]
        ):
            return TaskType.FILE_TRIAGE
        if any(word in text for word in ["summarise", "summarize", "recap", "overview"]):
            return TaskType.SUMMARY
        return TaskType.GENERAL

    def select_model(self, task: TaskType, requested_model: str | None = None) -> str:
        if requested_model:
            return requested_model
        return {
            TaskType.SUMMARY: self.settings.cheap_model,
            TaskType.FILE_TRIAGE: self.settings.balanced_model,
            TaskType.GENERAL: self.settings.default_model,
            TaskType.CODE: self.settings.code_model,
            TaskType.AUDIT: self.settings.audit_model,
            TaskType.PREMIUM: self.settings.premium_model,
        }[task]

    def fallback_models_for_task(self, task: TaskType, selected_model: str) -> list[str]:
        """Return ordered fallback models, excluding the model already selected.

        These defaults favour cheap/high-context models first, then premium aliases.
        Keep this list env-configurable rather than hard-coding the final model policy
        into application logic.
        """

        by_task = {
            TaskType.SUMMARY: [
                self.settings.cheap_model,
                self.settings.default_model,
                self.settings.openrouter_free_fallback_model,
            ],
            TaskType.GENERAL: [
                self.settings.default_model,
                self.settings.cheap_model,
                self.settings.balanced_model,
                self.settings.openrouter_free_fallback_model,
            ],
            TaskType.FILE_TRIAGE: [
                self.settings.balanced_model,
                self.settings.default_model,
                self.settings.openrouter_free_fallback_model,
            ],
            TaskType.CODE: [
                self.settings.code_model,
                self.settings.balanced_model,
                self.settings.premium_model,
                self.settings.openrouter_free_fallback_model,
            ],
            TaskType.AUDIT: [
                self.settings.audit_model,
                self.settings.premium_model,
                self.settings.balanced_model,
                self.settings.default_model,
                self.settings.openrouter_free_fallback_model,
            ],
            TaskType.PREMIUM: [
                self.settings.premium_model,
                self.settings.audit_model,
                self.settings.balanced_model,
                self.settings.openrouter_free_fallback_model,
            ],
        }[task]

        seen: set[str] = {selected_model}
        fallbacks: list[str] = []
        for model in by_task:
            if model and model not in seen:
                seen.add(model)
                fallbacks.append(model)
        return fallbacks

    def summarise_model(self, model: dict[str, Any]) -> dict[str, Any]:
        pricing = model.get("pricing") or {}
        return {
            "id": model.get("id"),
            "name": model.get("name"),
            "context_length": model.get("context_length"),
            "prompt_price": pricing.get("prompt"),
            "completion_price": pricing.get("completion"),
            "architecture": model.get("architecture"),
            "top_provider": model.get("top_provider"),
        }

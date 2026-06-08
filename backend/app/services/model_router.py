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

    def resolve_mode(self, task: TaskType, requested_mode: Mode) -> Mode:
        """Convert Auto mode into the best prompt mode for the classified task."""

        if requested_mode != Mode.AUTO:
            return requested_mode
        return {
            TaskType.CODE: Mode.CODE,
            TaskType.AUDIT: Mode.AUDIT,
            TaskType.FILE_TRIAGE: Mode.FILE_ANALYSIS,
            TaskType.SUMMARY: Mode.GENERAL,
            TaskType.GENERAL: Mode.GENERAL,
            TaskType.PREMIUM: Mode.AUDIT,
        }[task]

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

        Failed explicit models should not silently jump to a paid route during smoke
        tests. By default, the fallback ladder is free-first and free-only. Set
        ALLOW_PAID_FALLBACK=true when a production lane is allowed to escalate from a
        dead/overloaded model into paid alternatives.
        """

        by_task = {
            TaskType.SUMMARY: [
                self.settings.openrouter_free_fallback_model,
                self.settings.cheap_model,
                self.settings.default_model,
            ],
            TaskType.GENERAL: [
                self.settings.openrouter_free_fallback_model,
                self.settings.cheap_model,
                self.settings.default_model,
                self.settings.balanced_model,
            ],
            TaskType.FILE_TRIAGE: [
                self.settings.openrouter_free_fallback_model,
                self.settings.cheap_model,
                self.settings.balanced_model,
                self.settings.default_model,
            ],
            TaskType.CODE: [
                self.settings.openrouter_free_fallback_model,
                self.settings.cheap_model,
                self.settings.code_model,
                self.settings.balanced_model,
                self.settings.premium_model,
            ],
            TaskType.AUDIT: [
                self.settings.openrouter_free_fallback_model,
                self.settings.cheap_model,
                self.settings.audit_model,
                self.settings.balanced_model,
                self.settings.default_model,
                self.settings.premium_model,
            ],
            TaskType.PREMIUM: [
                self.settings.openrouter_free_fallback_model,
                self.settings.premium_model,
                self.settings.audit_model,
                self.settings.balanced_model,
            ],
        }[task]

        return self._dedupe_and_filter_fallbacks(by_task, selected_model)

    def _dedupe_and_filter_fallbacks(self, candidates: list[str], selected_model: str) -> list[str]:
        seen: set[str] = {selected_model}
        free_fallbacks: list[str] = []
        paid_fallbacks: list[str] = []

        for model in candidates:
            if not model or model in seen:
                continue
            seen.add(model)
            if self._is_free_model(model):
                free_fallbacks.append(model)
            elif self.settings.allow_paid_fallback:
                paid_fallbacks.append(model)

        return free_fallbacks + paid_fallbacks

    def _is_free_model(self, model: str) -> bool:
        return model == self.settings.openrouter_free_fallback_model or model.endswith(":free")

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

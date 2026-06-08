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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def classify_task(self, user_message: str, requested_mode: Mode = Mode.AUTO) -> TaskType:
        text = user_message.lower()
        if requested_mode == Mode.CODE or any(word in text for word in ["repo", "python", "javascript", "bug", "traceback", "ci error"]):
            return TaskType.CODE
        if requested_mode == Mode.AUDIT or any(word in text for word in ["aims", "rams", "audit", "quarantine", "koyeb", "gate"]):
            return TaskType.AUDIT
        if requested_mode == Mode.FILE_ANALYSIS or any(word in text for word in ["upload", "zip", "spreadsheet", "pdf", "file"]):
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

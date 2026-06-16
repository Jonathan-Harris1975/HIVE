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


MODEL_GROUP_ORDER = [
    "configured",
    "free",
    "reasoning",
    "coding",
    "documents",
    "vision",
    "video_analysis",
    "general",
    "audio",
    "image_generation",
    "video_generation",
    "other",
]

MODEL_GROUP_LABELS = {
    "configured": "HIVE configured",
    "free": "Free",
    "reasoning": "Reasoning",
    "coding": "Coding",
    "documents": "Long context & documents",
    "vision": "Vision / image analysis",
    "video_analysis": "Video analysis",
    "general": "General chat",
    "audio": "Audio & speech",
    "image_generation": "Image generation",
    "video_generation": "Video generation",
    "other": "Other models",
}


class ModelRouter:
    """Small, explicit model-policy layer plus safe model-discovery metadata."""

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
            if self._is_free_model_id(model):
                free_fallbacks.append(model)
            elif self.settings.allow_paid_fallback:
                paid_fallbacks.append(model)

        return free_fallbacks + paid_fallbacks

    def _is_free_model_id(self, model: str) -> bool:
        return model == self.settings.openrouter_free_fallback_model or model.endswith(":free")

    def configured_roles(self, model_id: str) -> list[str]:
        configured = {
            "default": self.settings.default_model,
            "cheap": self.settings.cheap_model,
            "balanced": self.settings.balanced_model,
            "code": self.settings.code_model,
            "audit": self.settings.audit_model,
            "premium": self.settings.premium_model,
            "free_fallback": self.settings.openrouter_free_fallback_model,
        }
        return [role for role, configured_id in configured.items() if configured_id == model_id]

    def summarise_model(self, model: dict[str, Any]) -> dict[str, Any]:
        model_id = str(model.get("id") or "")
        name = str(model.get("name") or model_id)
        description = str(model.get("description") or "")
        architecture = model.get("architecture") if isinstance(model.get("architecture"), dict) else {}
        input_modalities = self._string_list(architecture.get("input_modalities"))
        output_modalities = self._string_list(architecture.get("output_modalities"))
        modality = architecture.get("modality")
        if not input_modalities or not output_modalities:
            inferred_input, inferred_output = self._modalities_from_arrow(modality)
            input_modalities = input_modalities or inferred_input
            output_modalities = output_modalities or inferred_output
        supported_parameters = self._string_list(model.get("supported_parameters"))
        pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
        configured_roles = self.configured_roles(model_id)
        is_free = self._is_free_model(model_id, pricing)
        groups = self._model_groups(
            model_id=model_id,
            name=name,
            description=description,
            context_length=model.get("context_length"),
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            supported_parameters=supported_parameters,
            configured=bool(configured_roles),
            is_free=is_free,
        )
        discovery_group = next(
            (group for group in ["image_generation", "video_generation", "audio"] if group in groups),
            None,
        )
        primary_group = discovery_group or next(
            (group for group in MODEL_GROUP_ORDER if group in groups),
            "other",
        )
        chat_selectable, disabled_reason = self._chat_selection_policy(output_modalities)
        infrastructure_only = any(
            item in output_modalities for item in {"embeddings", "rerank", "transcription"}
        ) and "text" not in output_modalities

        return {
            "id": model_id,
            "name": name,
            "description": description or None,
            "context_length": model.get("context_length"),
            "max_completion_tokens": (model.get("top_provider") or {}).get("max_completion_tokens")
            if isinstance(model.get("top_provider"), dict)
            else None,
            "prompt_price": pricing.get("prompt"),
            "completion_price": pricing.get("completion"),
            "image_price": pricing.get("image"),
            "request_price": pricing.get("request"),
            "architecture": architecture,
            "input_modalities": input_modalities,
            "output_modalities": output_modalities,
            "supported_parameters": supported_parameters,
            "top_provider": model.get("top_provider"),
            "is_free": is_free,
            "configured_roles": configured_roles,
            "groups": groups,
            "primary_group": primary_group,
            "group_label": MODEL_GROUP_LABELS.get(primary_group, "Other models"),
            "chat_selectable": chat_selectable,
            "visible_in_chat_picker": not infrastructure_only,
            "disabled_reason": disabled_reason,
        }

    def model_group_manifest(self, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for group in MODEL_GROUP_ORDER:
            count = sum(1 for model in models if model.get("primary_group") == group)
            if count:
                payload.append(
                    {
                        "id": group,
                        "label": MODEL_GROUP_LABELS[group],
                        "count": count,
                        "chat_selectable_count": sum(
                            1
                            for model in models
                            if model.get("primary_group") == group and model.get("chat_selectable")
                        ),
                    }
                )
        return payload

    def _model_groups(
        self,
        *,
        model_id: str,
        name: str,
        description: str,
        context_length: Any,
        input_modalities: list[str],
        output_modalities: list[str],
        supported_parameters: list[str],
        configured: bool,
        is_free: bool,
    ) -> list[str]:
        text = f"{model_id} {name} {description}".lower()
        groups: set[str] = set()
        if configured:
            groups.add("configured")
        if is_free:
            groups.add("free")
        if "image" in output_modalities:
            groups.add("image_generation")
        if "video" in output_modalities:
            groups.add("video_generation")
        if "audio" in output_modalities or "speech" in output_modalities:
            groups.add("audio")
        if "video" in input_modalities and "text" in output_modalities:
            groups.add("video_analysis")
        if "image" in input_modalities and "text" in output_modalities:
            groups.add("vision")
        if "file" in input_modalities or self._int_value(context_length) >= 100_000:
            groups.add("documents")
        if "reasoning" in supported_parameters or any(
            token in text
            for token in ["reasoning", "thinking", "deepseek-r1", "/o1", "/o3", "/o4", " qwq", "r1-"]
        ):
            groups.add("reasoning")
        if any(
            token in text
            for token in ["coder", "coding", "code ", "codex", "devstral", "grok-build", "qwen3-coder"]
        ):
            groups.add("coding")
        if "text" in output_modalities and not groups.intersection(
            {"reasoning", "coding", "documents", "vision", "video_analysis"}
        ):
            groups.add("general")
        if not groups:
            groups.add("other")
        return [group for group in MODEL_GROUP_ORDER if group in groups]

    def _chat_selection_policy(self, output_modalities: list[str]) -> tuple[bool, str | None]:
        outputs = set(output_modalities)
        if "video" in outputs:
            return False, "Video generation needs the later dedicated creation workspace."
        if "image" in outputs:
            return False, "Image generation needs the later dedicated creation workspace."
        if outputs.intersection({"audio", "speech"}):
            return False, "Audio-output models are discovery-only in standard chat."
        if outputs.intersection({"embeddings", "rerank", "transcription"}) and "text" not in outputs:
            return False, "Infrastructure models are not available in the chat picker."
        if "text" not in outputs:
            return False, "This model does not provide text output for standard chat."
        return True, None

    def _is_free_model(self, model_id: str, pricing: dict[str, Any]) -> bool:
        if self._is_free_model_id(model_id):
            return True
        priced_fields = [pricing.get("prompt"), pricing.get("completion"), pricing.get("request")]
        present = [value for value in priced_fields if value not in {None, ""}]
        return bool(present) and all(self._float_value(value) == 0 for value in present)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip().lower() for item in value if str(item).strip()]

    @staticmethod
    def _modalities_from_arrow(value: Any) -> tuple[list[str], list[str]]:
        if not isinstance(value, str) or "->" not in value:
            return [], []
        left, right = value.split("->", 1)
        return (
            [part.strip().lower() for part in left.split("+") if part.strip()],
            [part.strip().lower() for part in right.split("+") if part.strip()],
        )

    @staticmethod
    def _float_value(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("inf")

    @staticmethod
    def _int_value(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

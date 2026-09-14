from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import Any

from app.core.config import Settings
from app.services import model_registry
from app.services.model_governance import (
    ModelSelectionDecision,
    ModelUseJustification,
    is_premium_model,
    lifecycle_is_routable,
    lifecycle_status,
    select_with_governance,
)


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
        if requested_mode in {Mode.AUDIT, Mode.BRAND} or any(
            word in text for word in ["aims", "rams", "audit", "quarantine", "koyeb", "gate"]
        ):
            return TaskType.AUDIT
        if requested_mode == Mode.CODE or any(
            word in text
            for word in ["repo", "python", "javascript", "bug", "traceback", "ci error"]
        ):
            return TaskType.CODE
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

    def _registry_default(self, category: str) -> str | None:
        """Return the best model only when it clears HIVE's quality floor.

        Low-ranked models remain in D1 for audit/history, but they must never
        silently become task defaults just because a category is populated.
        """
        ranked = model_registry.get_ranked_models(category)
        if not ranked:
            return None
        return next(
            (
                candidate.model_id
                for candidate in ranked
                if candidate.score >= self.settings.model_registry_min_visible_score
                and lifecycle_is_routable(candidate.lifecycle_status)
            ),
            None,
        )

    def _baseline_model(self, task: TaskType) -> str:
        category_by_task = {
            TaskType.SUMMARY: "cheap",
            TaskType.FILE_TRIAGE: "long_context",
            TaskType.GENERAL: "reasoning",
            TaskType.CODE: "coding",
            TaskType.AUDIT: "reasoning",
            TaskType.PREMIUM: "reasoning",
        }
        static_by_task = {
            TaskType.SUMMARY: self.settings.cheap_model,
            TaskType.FILE_TRIAGE: self.settings.balanced_model,
            TaskType.GENERAL: self.settings.default_model,
            TaskType.CODE: self.settings.code_model,
            TaskType.AUDIT: self.settings.audit_model,
            TaskType.PREMIUM: self.settings.premium_model,
        }
        return self._registry_default(category_by_task[task]) or static_by_task[task]

    def select_model(self, task: TaskType, requested_model: str | None = None) -> str:
        if requested_model:
            return requested_model

        return self._baseline_model(task)

    def select_model_decision(
        self,
        task: TaskType,
        requested_model: str | None = None,
        *,
        data_classification: str = "internal",
        justification: ModelUseJustification | None = None,
    ) -> ModelSelectionDecision:
        """Return the selected model together with persistent audit evidence."""

        baseline = self._baseline_model(task)
        decision = select_with_governance(
            task=str(task),
            requested_model=requested_model,
            baseline_model=baseline,
            data_classification=data_classification,
            justification=justification,
            settings=self.settings,
        )
        status = self._registered_lifecycle_status(decision.selected_model)
        if status is not None and not lifecycle_is_routable(status):
            return replace(
                decision,
                selected_model=baseline,
                selection_source="lifecycle_override_replaced_with_baseline",
                policy_issues=(*decision.policy_issues, f"model_lifecycle_{status}"),
            )
        return decision

    def _registered_lifecycle_status(self, model_id: str) -> str | None:
        for category in model_registry.CATEGORIES:
            for item in model_registry.get_ranked_models(category):
                if model_id in {item.model_id, item.canonical_slug}:
                    return item.lifecycle_status
        return None

    def progressive_models_for_task(
        self,
        task: TaskType,
        *,
        max_models: int = 4,
        allow_free: bool = False,
        allow_premium: bool = False,
    ) -> list[str]:
        """Return a bounded, cheapest-first escalation ladder for iterative work.

        Repository improvement loops need model progression to be explicit: a
        failed cheap attempt must not silently jump to an expert model through
        OpenRouter fallback handling.  The ladder therefore contains unique,
        routable candidates only and leaves premium models out unless the caller
        deliberately opts in.
        """
        limit = max(1, min(int(max_models), 4))
        if task == TaskType.CODE:
            candidates = [self.settings.cheap_model, self.settings.balanced_model]
            # Registry candidates are Council-ranked. Iterate weakest qualified
            # to strongest so each loop represents a genuine capability step.
            ranked = [
                item
                for item in reversed(model_registry.get_ranked_models("coding"))
                if item.score >= self.settings.model_registry_min_visible_score
                and lifecycle_is_routable(item.lifecycle_status)
            ]
            candidates.extend(item.model_id for item in ranked)
            candidates.append(self.settings.code_model)
        else:
            candidates = [
                self.settings.cheap_model,
                self.settings.balanced_model,
                self._baseline_model(task),
            ]

        ordered: list[str] = []
        seen: set[str] = set()
        for model in candidates:
            model = str(model or "").strip()
            if not model or model in seen:
                continue
            if not allow_free and self._is_free_model_id(model):
                continue
            if not allow_premium and is_premium_model(model, self.settings):
                continue
            status = self._registered_lifecycle_status(model)
            if status is not None and not lifecycle_is_routable(status):
                continue
            seen.add(model)
            ordered.append(model)

        if len(ordered) <= limit:
            return ordered
        # Preserve the cheapest starting point and the strongest final point
        # while bounding the number of paid calls.
        return [*ordered[: limit - 1], ordered[-1]]

    def council_models_for_task(
        self,
        task: TaskType,
        *,
        max_models: int = 2,
    ) -> list[str]:
        """Return the bounded expert-review ladder for post-loop councils.

        A council is intentionally separate from the self-improvement ladder.
        The first seat uses the configured audit model and the second may use the
        configured premium model.  Callers still decide whether a council is
        necessary; this method only enforces the hard two-model ceiling.
        """
        limit = max(0, min(int(max_models), 2))
        if limit == 0:
            return []
        candidates = [self.settings.audit_model]
        if task == TaskType.CODE:
            candidates.append(self.settings.code_model)
        candidates.append(self.settings.premium_model)

        ordered: list[str] = []
        seen: set[str] = set()
        for model in candidates:
            model = str(model or "").strip()
            if not model or model in seen or self._is_free_model_id(model):
                continue
            status = self._registered_lifecycle_status(model)
            if status is not None and not lifecycle_is_routable(status):
                continue
            seen.add(model)
            ordered.append(model)
        # Prefer the audit/code reviewer first, but reserve the configured
        # premium model for the final escalation when it is distinct.
        if len(ordered) <= limit:
            return ordered
        if self.settings.premium_model in ordered and limit >= 2:
            first = next(
                (model for model in ordered if model != self.settings.premium_model),
                ordered[0],
            )
            return [first, self.settings.premium_model]
        return ordered[:limit]

    def fallback_models_for_task(
        self,
        task: TaskType,
        selected_model: str,
        *,
        allow_free: bool = True,
        allow_premium: bool = False,
    ) -> list[str]:
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
        return self._dedupe_and_filter_fallbacks(
            by_task,
            selected_model,
            allow_free=allow_free,
            allow_premium=allow_premium,
        )

    def _dedupe_and_filter_fallbacks(
        self,
        candidates: list[str],
        selected_model: str,
        *,
        allow_free: bool,
        allow_premium: bool,
    ) -> list[str]:
        seen: set[str] = {selected_model}
        free_fallbacks: list[str] = []
        paid_fallbacks: list[str] = []

        for model in candidates:
            if not model or model in seen:
                continue
            seen.add(model)
            if self._is_free_model_id(model):
                if allow_free:
                    free_fallbacks.append(model)
            elif self.settings.allow_paid_fallback and (
                allow_premium or not is_premium_model(model, self.settings)
            ):
                paid_fallbacks.append(model)

        # When paid fallbacks are enabled, try the governed stable ladder
        # before the best-effort free endpoint. In free-only development mode,
        # preserve the free-only behaviour.
        return (
            paid_fallbacks + free_fallbacks if self.settings.allow_paid_fallback else free_fallbacks
        )

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
        raw_architecture = model.get("architecture")
        architecture: dict[str, Any] = (
            raw_architecture if isinstance(raw_architecture, dict) else {}
        )
        input_modalities = self._string_list(architecture.get("input_modalities"))
        output_modalities = self._string_list(architecture.get("output_modalities"))
        modality = architecture.get("modality")
        if not input_modalities or not output_modalities:
            inferred_input, inferred_output = self._modalities_from_arrow(modality)
            input_modalities = input_modalities or inferred_input
            output_modalities = output_modalities or inferred_output
        supported_parameters = self._string_list(model.get("supported_parameters"))
        raw_pricing = model.get("pricing")
        pricing: dict[str, Any] = raw_pricing if isinstance(raw_pricing, dict) else {}
        retirement_status, days_to_expiry = lifecycle_status(
            str(model.get("expiration_date")) if model.get("expiration_date") else None,
            watch_days=self.settings.model_retirement_watch_days,
            deprecating_days=self.settings.model_retirement_deprecating_days,
            quarantine_days=self.settings.model_retirement_quarantine_days,
        )
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
            (
                group
                for group in ["image_generation", "video_generation", "audio"]
                if group in groups
            ),
            None,
        )
        primary_group = discovery_group or next(
            (group for group in MODEL_GROUP_ORDER if group in groups),
            "other",
        )
        chat_selectable, disabled_reason = self._chat_selection_policy(output_modalities)

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
            "canonical_slug": model.get("canonical_slug"),
            "expiration_date": model.get("expiration_date"),
            "lifecycle_status": retirement_status,
            "days_to_expiry": days_to_expiry,
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
            "visible_in_chat_picker": True,
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
            for token in [
                "reasoning",
                "thinking",
                "deepseek-r1",
                "/o1",
                "/o3",
                "/o4",
                " qwq",
                "r1-",
            ]
        ):
            groups.add("reasoning")
        if any(
            token in text
            for token in [
                "coder",
                "coding",
                "code ",
                "codex",
                "devstral",
                "grok-build",
                "qwen3-coder",
            ]
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
        if "text" in outputs:
            return True, None
        if outputs.intersection({"image", "video"}):
            return (
                False,
                "Discovery-only in HIVE chat; use the future creation workspace for image or video generation.",
            )
        if outputs.intersection({"audio", "speech"}):
            return (
                True,
                "Enabled for explicit selection; non-text audio responses may require a dedicated renderer.",
            )
        if outputs.intersection({"embeddings", "rerank", "transcription"}):
            return True, "Enabled for explicit selection; this is an infrastructure-style model."
        return (
            True,
            "Enabled for explicit selection; output modality was not declared by the provider.",
        )

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

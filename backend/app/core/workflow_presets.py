from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import Settings
from app.services.model_router import Mode


@dataclass(frozen=True)
class WorkflowPreset:
    """Preset defaults for HIVE's file-chat workflows.

    Presets are intentionally small and deterministic. They tune retrieval,
    prompt framing, and free-tier limits without creating hidden automations.
    """

    name: str
    label: str
    description: str
    mode: Mode
    use_chunks: bool
    use_vectorize: bool
    vectorize_fallback_sql: bool
    auto_chunk: bool
    chunk_limit: int
    max_tokens: int
    temperature: float
    max_file_chars: int | None
    output_shape: str
    prompt_instruction: str
    free_tier_note: str

    def safe_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = str(self.mode)
        return data


BASE_FREE_TIER_NOTE = (
    "Designed for Koyeb free web-service use: bounded chunks, no large batch loops, "
    "and SQL fallback kept available."
)


_PRESETS: dict[str, WorkflowPreset] = {
    "audit_report_review": WorkflowPreset(
        name="audit_report_review",
        label="Audit report review",
        description="Summarise RAMS/AIMS audit bundles, production findings, QA issues, and next actions.",
        mode=Mode.AUDIT,
        use_chunks=True,
        use_vectorize=True,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=8,
        max_tokens=1400,
        temperature=0.25,
        max_file_chars=None,
        output_shape="summary, high-risk findings, evidence notes, recommendations, next checks",
        prompt_instruction=(
            "Treat this as an operational audit review. Prioritise production risk, broken workflows, "
            "quarantine/QA behaviour, deployment issues, missing evidence, and future-facing fixes. "
            "Separate confirmed findings from reasonable inferences."
        ),
        free_tier_note=BASE_FREE_TIER_NOTE,
    ),
    "repo_debug_bundle": WorkflowPreset(
        name="repo_debug_bundle",
        label="Repo/debug bundle",
        description="Inspect repo ZIPs, logs, stack traces, deployment artefacts, and failing-test bundles.",
        mode=Mode.CODE,
        use_chunks=True,
        use_vectorize=True,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=10,
        max_tokens=1600,
        temperature=0.15,
        max_file_chars=None,
        output_shape="root cause, affected files, safe patch plan, tests, rollback risk",
        prompt_instruction=(
            "Treat this as a code/debug review. Identify exact failure points where evidence allows, "
            "avoid guessing, mention affected files or config names when visible, and prefer minimal safe patches."
        ),
        free_tier_note=BASE_FREE_TIER_NOTE,
    ),
    "ci_log_analysis": WorkflowPreset(
        name="ci_log_analysis",
        label="CI/log analysis",
        description="Analyse CI logs, Koyeb logs, scheduler output, and runtime diagnostics.",
        mode=Mode.CODE,
        use_chunks=True,
        use_vectorize=False,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=8,
        max_tokens=1200,
        temperature=0.1,
        max_file_chars=None,
        output_shape="error summary, likely cause, exact next checks, safe fix order",
        prompt_instruction=(
            "Treat this as a log triage task. Extract the first meaningful error, repeated symptoms, "
            "timestamps if present, and the smallest next action. Do not over-explain passing log noise."
        ),
        free_tier_note=(
            "Uses SQL chunk retrieval by default because log terms are often exact and cheaper than semantic search."
        ),
    ),
    "social_content_qa": WorkflowPreset(
        name="social_content_qa",
        label="Social content QA",
        description="Review social posts, RSS rewrites, captions, hooks, and brand-safety output.",
        mode=Mode.BRAND,
        use_chunks=True,
        use_vectorize=True,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=6,
        max_tokens=1200,
        temperature=0.3,
        max_file_chars=None,
        output_shape="brand fit, clarity, risk flags, future QA improvements, suggested guardrails",
        prompt_instruction=(
            "Treat this as Jonathan Harris ecosystem content QA. Focus on brand consistency, clarity, "
            "engagement quality, platform suitability, and future-facing QA improvements rather than rewriting old posts."
        ),
        free_tier_note=BASE_FREE_TIER_NOTE,
    ),
    "podcast_episode_review": WorkflowPreset(
        name="podcast_episode_review",
        label="Podcast episode review",
        description="Review podcast transcripts, episode metadata, RSS wording, and amplification opportunities.",
        mode=Mode.BRAND,
        use_chunks=True,
        use_vectorize=True,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=8,
        max_tokens=1400,
        temperature=0.25,
        max_file_chars=None,
        output_shape="episode summary, standout points, metadata/SEO notes, promotion angles, QA flags",
        prompt_instruction=(
            "Treat this as a podcast operations review. Look for transcript quality, metadata clarity, "
            "SEO/fresh-signal opportunities, and safe ways AIMS could amplify the episode."
        ),
        free_tier_note=BASE_FREE_TIER_NOTE,
    ),
    "ebook_keyword_review": WorkflowPreset(
        name="ebook_keyword_review",
        label="eBook keyword review",
        description="Review KDP/eBook keyword notes, catalogue gaps, positioning, and niche signals.",
        mode=Mode.BRAND,
        use_chunks=True,
        use_vectorize=True,
        vectorize_fallback_sql=True,
        auto_chunk=True,
        chunk_limit=8,
        max_tokens=1400,
        temperature=0.25,
        max_file_chars=None,
        output_shape="keyword observations, niche gaps, catalogue fit, caution notes, next research checks",
        prompt_instruction=(
            "Treat this as an evidence-led KDP/eBook keyword review. Do not invent market data. "
            "Separate document-supported keyword findings from areas that need live marketplace research."
        ),
        free_tier_note=BASE_FREE_TIER_NOTE,
    ),
}


ALIASES: dict[str, str] = {
    "audit": "audit_report_review",
    "audits": "audit_report_review",
    "rams": "audit_report_review",
    "repo": "repo_debug_bundle",
    "debug": "repo_debug_bundle",
    "ci": "ci_log_analysis",
    "logs": "ci_log_analysis",
    "social": "social_content_qa",
    "content": "social_content_qa",
    "podcast": "podcast_episode_review",
    "ebook": "ebook_keyword_review",
    "kdp": "ebook_keyword_review",
}


def normalise_preset_name(name: str | None) -> str | None:
    if not name:
        return None
    clean = name.strip().lower().replace(" ", "_").replace("-", "_")
    if clean in {"none", "off", "false", "raw"}:
        return None
    return ALIASES.get(clean, clean)


def get_workflow_preset(name: str | None) -> WorkflowPreset | None:
    clean = normalise_preset_name(name)
    if not clean:
        return None
    return _PRESETS.get(clean)


def allowed_workflow_presets() -> list[str]:
    return sorted(_PRESETS)


def workflow_presets_payload(settings: Settings) -> dict[str, Any]:
    return {
        "ok": True,
        "build_stage_hint": "v1.12-shared-ecosystem-execution-layer",
        "free_tier": settings.hive_free_tier_mode,
        "default_retrieval": "hybrid vector + SQL fallback" if settings.vectorize_enabled else "SQL fallback",
        "presets": [preset.safe_dict() for preset in _PRESETS.values()],
        "aliases": ALIASES,
    }


def apply_workflow_preset_to_request(request, preset: WorkflowPreset):  # noqa: ANN001, ANN201
    """Return a request copy with preset defaults applied.

    For v1.6, a named preset deliberately wins over generic request defaults.
    Advanced callers can omit workflow_preset/null it when they want raw control.
    """

    return request.model_copy(
        update={
            "mode": preset.mode,
            "use_chunks": preset.use_chunks,
            "use_vectorize": preset.use_vectorize,
            "vectorize_fallback_sql": preset.vectorize_fallback_sql,
            "auto_chunk": preset.auto_chunk,
            "chunk_limit": preset.chunk_limit,
            "max_tokens": preset.max_tokens,
            "temperature": preset.temperature,
            "max_file_chars": request.max_file_chars or preset.max_file_chars,
        }
    )

"""Tests for bounded Headroom context optimisation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.core.config import Settings
from app.services.headroom_optimizer import HeadroomOptimizer


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "Keep this instruction byte-for-byte."},
        {"role": "assistant", "content": '{"items":[1,2,3,4,5]}' * 120},
        {"role": "user", "content": "Current request must remain intact."},
    ]


def test_disabled_passthrough() -> None:
    settings = Settings(headroom_enabled=False)
    optimizer = HeadroomOptimizer(settings)
    messages = _messages()

    outcome = optimizer.optimise(messages, model="test/model")

    assert outcome.messages == messages
    assert outcome.attempted is False
    assert outcome.skipped_reason == "disabled"


def test_exact_context_passthrough() -> None:
    optimizer = HeadroomOptimizer(Settings())
    messages = _messages()

    outcome = optimizer.optimise(messages, model="test/model", exact_context=True)

    assert outcome.messages == messages
    assert outcome.attempted is False
    assert outcome.skipped_reason == "exact_context"


def test_oversize_context_passthrough() -> None:
    settings = Settings(headroom_max_input_chars=16_384)
    optimizer = HeadroomOptimizer(settings)
    messages = [{"role": "user", "content": "x" * 16_385}]

    outcome = optimizer.optimise(messages, model="test/model")

    assert outcome.messages == messages
    assert outcome.attempted is False
    assert outcome.skipped_reason == "oversize"


def test_compression_preserves_system_and_records_savings() -> None:
    settings = Settings(headroom_protect_recent=1)
    optimizer = HeadroomOptimizer(settings)
    calls: list[dict[str, Any]] = []

    def fake_compress(messages: list[dict[str, str]], **kwargs: Any) -> Any:
        calls.append({"messages": messages, **kwargs})
        return SimpleNamespace(
            messages=[
                dict(messages[0]),
                {"role": "assistant", "content": '{"items":[1,2,3]}'},
                dict(messages[2]),
            ],
            tokens_before=1000,
            tokens_after=640,
            tokens_saved=360,
            transforms_applied=["smartcrusher"],
        )

    optimizer._compressor = fake_compress
    outcome = optimizer.optimise(_messages(), model="openai/test")

    assert outcome.compressed is True
    assert outcome.tokens_saved == 360
    assert outcome.messages[0]["content"] == "Keep this instruction byte-for-byte."
    assert outcome.messages[-1]["content"] == "Current request must remain intact."
    assert outcome.transforms == ("smartcrusher",)
    assert calls[0]["model"] == "openai/test"
    assert calls[0]["compress_user_messages"] is True
    assert calls[0]["compress_system_messages"] is False
    assert calls[0]["protect_recent"] == 1
    assert calls[0]["protect_analysis_context"] is True
    assert calls[0]["kompress_model"] == "disabled"


def test_changed_context_without_verified_saving_is_reverted() -> None:
    optimizer = HeadroomOptimizer(Settings())
    original = _messages()

    def fake_compress(messages: list[dict[str, str]], **_: Any) -> Any:
        return SimpleNamespace(
            messages=[
                dict(messages[0]),
                {"role": "assistant", "content": "changed"},
                dict(messages[2]),
            ],
            tokens_before=1000,
            tokens_after=1000,
            tokens_saved=0,
            transforms_applied=["smartcrusher"],
        )

    optimizer._compressor = fake_compress
    outcome = optimizer.optimise(original, model="test/model")

    assert outcome.messages == original
    assert outcome.compressed is False
    assert outcome.tokens_saved == 0
    assert outcome.skipped_reason == "no_verified_saving"


def test_system_mutation_is_rejected_and_originals_are_used() -> None:
    optimizer = HeadroomOptimizer(Settings())
    original = _messages()

    def fake_compress(messages: list[dict[str, str]], **_: Any) -> Any:
        return SimpleNamespace(
            messages=[
                {"role": "system", "content": "mutated"},
                dict(messages[1]),
                dict(messages[2]),
            ],
            tokens_before=1000,
            tokens_after=600,
            tokens_saved=400,
            transforms_applied=["unexpected"],
        )

    optimizer._compressor = fake_compress
    outcome = optimizer.optimise(original, model="test/model")

    assert outcome.messages == original
    assert outcome.failed is True
    assert outcome.skipped_reason == "error"


def test_inflation_guard_reverts_to_originals() -> None:
    optimizer = HeadroomOptimizer(Settings())
    original = _messages()

    def fake_compress(messages: list[dict[str, str]], **_: Any) -> Any:
        return SimpleNamespace(
            messages=[
                dict(messages[0]),
                {"role": "assistant", "content": "longer"},
                dict(messages[2]),
            ],
            tokens_before=1000,
            tokens_after=1100,
            tokens_saved=0,
            transforms_applied=["inflating-transform"],
        )

    optimizer._compressor = fake_compress
    outcome = optimizer.optimise(original, model="test/model")

    assert outcome.messages == original
    assert outcome.compressed is False
    assert outcome.tokens_after == 1000
    assert outcome.skipped_reason == "inflation_guard"


def test_ccr_retrieval_marker_is_rejected_without_tool_loop() -> None:
    optimizer = HeadroomOptimizer(Settings())
    original = _messages()

    def fake_compress(messages: list[dict[str, str]], **_: Any) -> Any:
        return SimpleNamespace(
            messages=[
                dict(messages[0]),
                {
                    "role": "assistant",
                    "content": "summary only\n<<ccr:abc123>> Retrieve more: hash=abc123",
                },
                dict(messages[2]),
            ],
            tokens_before=1000,
            tokens_after=300,
            tokens_saved=700,
            transforms_applied=["smartcrusher"],
        )

    optimizer._compressor = fake_compress
    outcome = optimizer.optimise(original, model="test/model")

    assert outcome.messages == original
    assert outcome.compressed is False
    assert outcome.tokens_saved == 0
    assert outcome.skipped_reason == "retrieval_marker"

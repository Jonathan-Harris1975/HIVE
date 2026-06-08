from app.services.context_manager import ContextWindow, estimate_tokens


def test_estimate_tokens_never_zero() -> None:
    assert estimate_tokens("") == 1


def test_context_trimming_keeps_recent_messages() -> None:
    window = ContextWindow(max_tokens=20, reserve_for_completion=5)
    window.add("user", "first " * 20)
    window.add("assistant", "middle")
    window.add("user", "latest")
    messages = window.trimmed_messages()
    assert messages[-1]["content"] == "latest"

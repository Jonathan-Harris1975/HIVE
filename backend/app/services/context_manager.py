from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    role: str
    content: str
    approx_tokens: int = 0


@dataclass
class ContextWindow:
    max_tokens: int = 120_000
    reserve_for_completion: int = 8_000
    messages: list[ChatMessage] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.messages.append(ChatMessage(role=role, content=content, approx_tokens=estimate_tokens(content)))

    @property
    def budget(self) -> int:
        return max(0, self.max_tokens - self.reserve_for_completion)

    def trimmed_messages(self) -> list[dict[str, str]]:
        total = 0
        selected: list[ChatMessage] = []
        for message in reversed(self.messages):
            if total + message.approx_tokens > self.budget and selected:
                break
            selected.append(message)
            total += message.approx_tokens
        return [{"role": m.role, "content": m.content} for m in reversed(selected)]


def estimate_tokens(text: str) -> int:
    # Fast approximation. Precise model-specific accounting can be added with tiktoken/openrouter usage.
    return max(1, len(text) // 4)

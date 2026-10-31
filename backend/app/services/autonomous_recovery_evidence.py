"""Immutable recovery evidence identifiers and redacted operator summaries."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re


_ALLOWED_OUTCOMES = frozenset({"held", "failed", "escalated", "verified"})
_SENSITIVE = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
)


def evidence_key(incident_id: str, revision: str, attempt: int, content: bytes) -> str:
    """Content-addressed R2 object key; caller must enforce conditional create."""
    if not incident_id or not revision or attempt < 1 or not isinstance(content, bytes):
        raise ValueError("invalid evidence identity")
    digest = sha256(content).hexdigest()
    return f"recovery/{incident_id}/{revision}/{attempt}/{digest}.json"


@dataclass(frozen=True)
class RecoveryReceipt:
    incident_id: str
    revision: str
    attempt: int
    outcome: str
    evidence_sha256: str
    deployment_revision: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.incident_id or not self.revision or self.attempt < 1
            or self.outcome not in _ALLOWED_OUTCOMES
            or not re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256)
        ):
            raise ValueError("invalid recovery receipt")
        if self.outcome == "verified" and self.deployment_revision != self.revision:
            raise ValueError("verified receipt requires exact deployed revision")

    def public_summary(self) -> dict[str, object]:
        return {
            "incident_id": self.incident_id,
            "revision": self.revision,
            "attempt": self.attempt,
            "outcome": self.outcome,
            "evidence_sha256": self.evidence_sha256,
            "deployment_verified": self.outcome == "verified",
        }


def sanitise_failure(message: str, *, limit: int = 240) -> str:
    if limit < 16:
        raise ValueError("limit too small")
    return _SENSITIVE.sub(lambda m: m.group(1) + "=[REDACTED]", message)[:limit]


def receipt_payload(receipt: RecoveryReceipt) -> bytes:
    return json.dumps(receipt.public_summary(), sort_keys=True, separators=(",", ":")).encode()

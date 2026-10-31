"""Credential-free evidence receipt contract tests."""
from hashlib import sha256

import pytest

from app.services.autonomous_recovery_evidence import (
    RecoveryReceipt,
    evidence_key,
    receipt_payload,
    sanitise_failure,
)


def test_content_addressed_evidence_key():
    assert evidence_key("id", "sha", 1, b"a") != evidence_key("id", "sha", 1, b"b")
    with pytest.raises(ValueError):
        evidence_key("id", "sha", 0, b"a")


def test_verified_requires_exact_deployed_revision():
    digest = sha256(b"evidence").hexdigest()
    with pytest.raises(ValueError):
        RecoveryReceipt("id", "sha", 1, "verified", digest, "different")
    receipt = RecoveryReceipt("id", "sha", 1, "verified", digest, "sha")
    assert receipt.public_summary()["deployment_verified"] is True
    assert b"deployment_verified" in receipt_payload(receipt)


def test_secret_redaction():
    message = "authorization: Bearer123 token=abc password=hunter2"
    clean = sanitise_failure(message)
    assert "Bearer123" not in clean
    assert "abc" not in clean
    assert "hunter2" not in clean

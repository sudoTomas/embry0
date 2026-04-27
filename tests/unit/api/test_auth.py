import hashlib
import hmac

import pytest
from fastapi import HTTPException

from athanor.api.auth import verify_api_key, verify_webhook_signature


def test_verify_api_key_valid():
    verify_api_key(api_key="test-key", authorization="Bearer test-key", dev_mode=False)


def test_verify_api_key_invalid():
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(api_key="real-key", authorization="Bearer wrong-key", dev_mode=False)
    assert exc_info.value.status_code == 401


def test_verify_api_key_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(api_key="real-key", authorization="", dev_mode=False)
    assert exc_info.value.status_code == 401


def test_verify_api_key_dev_mode_skips():
    verify_api_key(api_key="", authorization="", dev_mode=True)


def test_verify_api_key_no_key_configured():
    verify_api_key(api_key="", authorization="", dev_mode=False)


def test_verify_webhook_signature_valid():
    secret = "test-secret"
    body = b'{"action": "labeled"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    verify_webhook_signature(body=body, signature=sig, secret=secret)


def test_verify_webhook_signature_invalid():
    with pytest.raises(HTTPException) as exc_info:
        verify_webhook_signature(body=b"body", signature="sha256=invalid", secret="secret")
    assert exc_info.value.status_code == 401


def test_verify_webhook_signature_missing():
    with pytest.raises(HTTPException) as exc_info:
        verify_webhook_signature(body=b"body", signature="", secret="secret")
    assert exc_info.value.status_code == 401
    assert "Missing" in exc_info.value.detail


def test_verify_webhook_signature_dev_mode_no_secret_passes():
    """In dev_mode with no secret, skip verification (smee.io flow)."""
    # Should not raise
    verify_webhook_signature(body=b'{"action": "opened"}', signature="", secret="", dev_mode=True)


def test_verify_webhook_signature_no_secret_prod_mode_raises_503():
    """Without dev_mode and no secret, raise 503 (not configured)."""
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(body=b'{"action": "opened"}', signature="", secret="", dev_mode=False)
    assert exc.value.status_code == 503


def test_verify_webhook_signature_secret_set_dev_mode_still_verifies():
    """If a secret IS configured, dev_mode does NOT bypass — HMAC still enforced."""
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(
            body=b'{"action": "opened"}', signature="sha256=bogus", secret="real-secret", dev_mode=True
        )
    assert exc.value.status_code == 401

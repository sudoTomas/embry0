import hashlib
import hmac

import pytest
from fastapi import HTTPException

from embry0.api.auth import verify_api_key, verify_webhook_signature

# ---------------------------------------------------------------------------
# verify_api_key
# ---------------------------------------------------------------------------


def test_verify_api_key_auth_dev_mode_bypasses():
    """auth_dev_mode=True skips key check entirely."""
    verify_api_key(api_key="real-key", authorization="", auth_dev_mode=True)  # no raise


def test_verify_api_key_no_key_configured_passes():
    """Empty api_key allows requests (configuration absent)."""
    verify_api_key(api_key="", authorization="", auth_dev_mode=False)  # no raise


def test_verify_api_key_missing_bearer_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_api_key(api_key="real-key", authorization="", auth_dev_mode=False)
    assert exc.value.status_code == 401


def test_verify_api_key_wrong_token_rejected():
    with pytest.raises(HTTPException) as exc:
        verify_api_key(api_key="real-key", authorization="Bearer wrong", auth_dev_mode=False)
    assert exc.value.status_code == 401


def test_verify_api_key_correct_token_passes():
    verify_api_key(api_key="real-key", authorization="Bearer real-key", auth_dev_mode=False)


# ---------------------------------------------------------------------------
# verify_webhook_signature
# ---------------------------------------------------------------------------


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


def test_verify_webhook_signature_no_secret_dev_mode_passes():
    """In webhook_dev_mode with no secret, skip verification (smee.io flow)."""
    # Should not raise
    verify_webhook_signature(body=b'{"action": "opened"}', signature="", secret="", webhook_dev_mode=True)


def test_verify_webhook_signature_no_secret_prod_mode_raises_503():
    """Without webhook_dev_mode and no secret, raise 503 (not configured)."""
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(body=b'{"action": "opened"}', signature="", secret="", webhook_dev_mode=False)
    assert exc.value.status_code == 503


def test_verify_webhook_signature_secret_set_dev_mode_still_verifies():
    """If a secret IS configured, webhook_dev_mode does NOT bypass — HMAC still enforced."""
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(
            body=b'{"action": "opened"}', signature="sha256=bogus", secret="real-secret", webhook_dev_mode=True
        )
    assert exc.value.status_code == 401


def test_verify_webhook_signature_secret_set_correct_signature_passes():
    body = b"hello"
    secret = "real-secret"
    sig = "sha256=" + hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()
    verify_webhook_signature(body=body, signature=sig, secret=secret, webhook_dev_mode=False)


def test_independence_of_flags():
    """auth_dev_mode and webhook_dev_mode are truly independent."""
    # Auth bypassed but webhook check still active (no secret, no webhook_dev_mode)
    verify_api_key(api_key="real-key", authorization="", auth_dev_mode=True)
    with pytest.raises(HTTPException) as exc:
        verify_webhook_signature(body=b"x", signature="", secret="", webhook_dev_mode=False)
    assert exc.value.status_code == 503

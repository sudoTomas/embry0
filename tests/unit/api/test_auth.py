import hashlib
import hmac

import pytest
from fastapi import HTTPException

from legion.api.auth import verify_api_key, verify_webhook_signature


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

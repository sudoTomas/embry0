import pytest
from pydantic import ValidationError

from embry0.api.schemas import SandboxProfileRequest


def test_request_accepts_qa_fields():
    req = SandboxProfileRequest(
        name="qa-jvm",
        base_image="embry0-sandbox-qa:latest",
        description="JVM + Node QA",
        dind_enabled=True,
        idle_timeout_seconds=900,
        extra_networks=["backend"],
        env_defaults={"LANG": "C.UTF-8"},
    )
    assert req.dind_enabled is True
    assert req.idle_timeout_seconds == 900


def test_request_defaults_preserve_legacy_callers():
    # No new fields supplied — should validate with safe defaults.
    req = SandboxProfileRequest(name="legacy")
    assert req.description == ""
    assert req.dind_enabled is False
    assert req.idle_timeout_seconds == 600
    assert req.extra_networks == []
    assert req.env_defaults == {}


def test_request_rejects_is_builtin_from_user():
    """Users cannot mark their custom profile as builtin via the API."""
    with pytest.raises(ValidationError):
        SandboxProfileRequest(name="hax", is_builtin=True)


def test_request_validates_idle_timeout_positive():
    with pytest.raises(ValidationError):
        SandboxProfileRequest(name="bad", idle_timeout_seconds=0)

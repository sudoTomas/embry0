import pytest
from pydantic import ValidationError

from athanor.api.schemas.environment import EnvVarInput, EnvVarResponse


def test_input_accepts_qa_scope():
    v = EnvVarInput(key="QA_TEST_USER", value="x", scope="qa")
    assert v.scope == "qa"


def test_input_default_scope_is_app():
    v = EnvVarInput(key="DB_URL", value="x")
    assert v.scope == "app"


def test_input_rejects_unknown_scope():
    with pytest.raises(ValidationError):
        EnvVarInput(key="X", value="y", scope="prod")


def test_response_includes_scope():
    r = EnvVarResponse(key="K", value="V", var_type="config", description="", scope="qa")
    assert r.scope == "qa"


def test_qa_scope_requires_qa_prefix():
    """Cosmetic guard — keys with scope=qa should match QA_*."""
    with pytest.raises(ValidationError, match=r"QA_"):
        EnvVarInput(key="DB_URL", value="x", scope="qa")
    # OK
    EnvVarInput(key="QA_DB_URL", value="x", scope="qa")


def test_app_scope_does_not_require_prefix():
    EnvVarInput(key="DB_URL", value="x", scope="app")

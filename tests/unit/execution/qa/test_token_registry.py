import pytest

from athanor.execution.qa.presign import PresignAuthError
from athanor.execution.qa.token_registry import SandboxTokenRegistry


@pytest.mark.asyncio
async def test_register_and_lookup():
    reg = SandboxTokenRegistry()
    reg.register("tok-1", job_id="JOB", attempt_n=2)
    assert await reg.lookup("tok-1") == ("JOB", 2)


@pytest.mark.asyncio
async def test_lookup_unknown_token_raises():
    reg = SandboxTokenRegistry()
    with pytest.raises(PresignAuthError):
        await reg.lookup("missing")


@pytest.mark.asyncio
async def test_unregister_removes_token():
    reg = SandboxTokenRegistry()
    reg.register("t", job_id="J", attempt_n=1)
    reg.unregister("t")
    with pytest.raises(PresignAuthError):
        await reg.lookup("t")


@pytest.mark.asyncio
async def test_double_register_overwrites():
    reg = SandboxTokenRegistry()
    reg.register("t", job_id="A", attempt_n=1)
    reg.register("t", job_id="B", attempt_n=2)
    assert await reg.lookup("t") == ("B", 2)

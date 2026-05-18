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


def test_register_unregister_are_sync_not_coroutines():
    """Regression 2026-05-18: subtask_nodes.py awaited the synchronous
    unregister(), raising "object NoneType can't be used in 'await'
    expression" and silently leaking the token entry. Lock the contract:
    register/unregister are plain sync methods (callers must NOT await),
    while lookup IS a coroutine."""
    import inspect

    assert not inspect.iscoroutinefunction(SandboxTokenRegistry.register)
    assert not inspect.iscoroutinefunction(SandboxTokenRegistry.unregister)
    assert inspect.iscoroutinefunction(SandboxTokenRegistry.lookup)

    reg = SandboxTokenRegistry()
    reg.register("t", job_id="J", attempt_n=1)
    # Returns None synchronously — awaiting None is exactly the bug.
    assert reg.unregister("t") is None


def test_subtask_nodes_does_not_await_unregister():
    """Source guard: the subtask cleanup paths must call unregister
    synchronously (no `await`), matching the sync registry contract."""
    import pathlib

    src = pathlib.Path(
        "athanor/workflows/qa/subtask_nodes.py"
    ).read_text()
    assert "await token_registry.unregister(" not in src
    assert "await qa_token_registry.unregister(" not in src

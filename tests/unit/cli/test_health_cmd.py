"""``embry0 health`` reads the readiness check with the configured key.

Without a key the check answers its overall state alone; with one it also
lists each watched stack's state. The command sends the key it resolves as
every other command does (``--api-key``, ``EMBRY0_API_KEY``, ``API_KEY``,
then ``./.env``), and its request reaches the real route here, on the app of
``tests/unit/api/v1/test_health_ready_callers.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from embry0.api.deps import bust_api_key_cache, bust_user_gate_cache
from embry0.cli import __main__ as cli
from tests.unit.api.v1.test_health_ready_callers import MASTER, _app

HINT = "each watched stack's state is shown with a key"


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    bust_api_key_cache()
    bust_user_gate_cache()
    yield
    bust_api_key_cache()
    bust_user_gate_cache()


def _through_app(sent: list[dict[str, str]]) -> Callable[..., httpx.Response]:
    """``httpx.get`` answered by the app's readiness route, keeping the headers of each request."""
    client = TestClient(_app())

    def get(url: str, *, headers: dict[str, str] | None = None, **_kwargs: Any) -> httpx.Response:
        sent.append(dict(headers or {}))
        return client.get(httpx.URL(url).path, headers=headers)

    return get


def _health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str], env: dict[str, str]
) -> tuple[int, list[dict[str, str]]]:
    monkeypatch.chdir(tmp_path)
    for var in ("EMBRY0_API_KEY", "API_KEY", "EMBRY0_URL"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(httpx, "get", _through_app(sent))
    monkeypatch.setattr("sys.argv", ["embry0", "health", "--url", "http://orchestrator.test", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return int(exc.value.code or 0), sent


@pytest.mark.parametrize(
    ("label", "argv", "env", "dotenv"),
    [
        ("--api-key", ["--api-key", MASTER], {}, None),
        ("EMBRY0_API_KEY", [], {"EMBRY0_API_KEY": MASTER}, None),
        ("API_KEY", [], {"API_KEY": MASTER}, None),
        ("./.env", [], {}, f"API_KEY={MASTER}\n"),
    ],
)
def test_the_command_sends_the_configured_key_and_lists_each_stack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    label: str,
    argv: list[str],
    env: dict[str, str],
    dotenv: str | None,
) -> None:
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    code, sent = _health(monkeypatch, tmp_path, argv, env)
    out = capsys.readouterr().out
    assert sent == [{"Authorization": f"Bearer {MASTER}"}], f"{label}: the command sends the key it resolves: {sent}"
    assert "watcher_stacks" in out and "globex/gizmos" in out and HINT not in out, (
        f"{label}: with the key, each watched stack's state is printed: {out}"
    )
    assert code == 1, "a degraded check exits 1"


def test_without_a_key_the_command_prints_the_overall_state_and_how_to_see_more(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, sent = _health(monkeypatch, tmp_path, [], {})
    out = capsys.readouterr().out
    assert sent == [{}], f"no key is configured, so none is sent: {sent}"
    assert "watcher: invalid_logql" in out and "gizmos" not in out and HINT in out, (
        f"without a key the command prints the overall state and how to see each stack: {out}"
    )
    assert code == 1, "a degraded check exits 1 without a key too"

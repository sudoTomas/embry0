"""One set of exit codes for the document CLIs (#237).

``embry0 settings`` and ``embry0 progression-pack`` read a document from a
file, check it, diff it and write it through the API. A script that drives
one must be able to drive the other, so each outcome below is run through
both commands and must exit with the same code. The numbers are written
out, not read from :mod:`embry0.cli.exit_codes`: they are what scripts
test, so changing one must fail here.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import socket
from argparse import Namespace
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from embry0.cli import progression_pack, settings
from embry0.services.progression_pack import default_text

#: The scheme scripts rely on.
SCHEME = {"OK": 0, "REFUSED": 1, "USAGE": 2, "CONFLICT": 3, "UNAVAILABLE": 4}


@dataclass(frozen=True)
class _Cli:
    command: str
    run: Callable[..., int]
    good: str  # a file ``check`` accepts
    bad: str  # a file ``check`` refuses
    reads: int  # requests ``apply`` sends before its write: the dry run, and for settings the generation first

    def __str__(self) -> str:
        return self.command


SETTINGS = _Cli("settings", settings.run, "asks:\n  enabled: true\n", "asks:\n  enabled: maybe\n", reads=2)
PACK = _Cli(
    "progression-pack",
    progression_pack.run,
    default_text(),
    default_text().replace("version: 1\n", "version: 1\nmystery: 1\n", 1),
    reads=1,
)
BOTH = pytest.mark.parametrize("cli", [SETTINGS, PACK], ids=str)


def _args(action: str, **kw: Any) -> Namespace:
    """The arguments of either command's verbs; each reads only its own."""
    defaults = {"yes": False, "allow_unset": False, "reason": "", "default": False, "output": None, "id": None}
    return Namespace(action=action, **{**defaults, **kw})


def _file(tmp_path: Path, text: str) -> str:
    path = tmp_path / "document.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _would_change() -> httpx.Response:
    """A read or dry run of either API, as each command reads it: version 4, and the file would change it."""
    return httpx.Response(
        200,
        json={"would_change": True, "version": 4, "generation": 4, "changes": [], "sections": [], "text_diff": ""},
        headers={"ETag": '"4"'},
    )


def _api(*answers: httpx.Response) -> httpx.MockTransport:
    queue = list(answers)

    def answer(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if queue else httpx.Response(599, json={"detail": "the test queued no answer"})

    return httpx.MockTransport(answer)


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _check_valid(cli: _Cli, tmp_path: Path) -> int:
    return cli.run(_args("check", file=_file(tmp_path, cli.good)))


def _check_problems(cli: _Cli, tmp_path: Path) -> int:
    return cli.run(_args("check", file=_file(tmp_path, cli.bad)))


def _diff_answered(status: int) -> Callable[[_Cli, Path], int]:
    def outcome(cli: _Cli, tmp_path: Path) -> int:
        answer = httpx.Response(status, json={"detail": "no"})
        return cli.run(_args("diff", file=_file(tmp_path, cli.good)), transport=_api(answer))

    return outcome


def _diff_unreachable(cli: _Cli, tmp_path: Path) -> int:
    return cli.run(_args("diff", file=_file(tmp_path, cli.good)), transport=httpx.MockTransport(_down))


def _diff_of_a_missing_file(cli: _Cli, tmp_path: Path) -> int:
    return cli.run(_args("diff", file=str(tmp_path / "missing.yaml")), transport=_api())


def _export_to_a_missing_directory(cli: _Cli, tmp_path: Path) -> int:
    out = str(tmp_path / "no-such-directory" / "out.yaml")
    return cli.run(_args("export", output=out), transport=_api(httpx.Response(200, text=cli.good)))


def _apply_unconfirmed(cli: _Cli, tmp_path: Path) -> int:
    """``apply`` without ``--yes``: it asks on a terminal, and the test answers no (or has no terminal)."""
    answers = [_would_change() for _ in range(cli.reads)]
    return cli.run(_args("apply", file=_file(tmp_path, cli.good)), transport=_api(*answers))


def _apply_over_a_newer_document(cli: _Cli, tmp_path: Path) -> int:
    answers = [_would_change() for _ in range(cli.reads)]
    conflict = httpx.Response(409, json={"detail": "it changed since you read it"})
    return cli.run(_args("apply", file=_file(tmp_path, cli.good), yes=True), transport=_api(*answers, conflict))


#: (outcome, how to reach it, whether stdin is a terminal answering no, the code it must exit with)
OUTCOMES: list[tuple[str, Callable[[_Cli, Path], int], bool, int]] = [
    ("check: valid", _check_valid, False, 0),
    ("check: problems", _check_problems, False, 1),
    ("diff: 422", _diff_answered(422), False, 1),
    ("diff: 413", _diff_answered(413), False, 1),
    ("apply: answered no", _apply_unconfirmed, True, 1),
    ("diff: a file that cannot be read", _diff_of_a_missing_file, False, 2),
    ("export: a file that cannot be written", _export_to_a_missing_directory, False, 2),
    ("apply: no terminal and no --yes", _apply_unconfirmed, False, 2),
    ("apply: changed since the diff (409)", _apply_over_a_newer_document, False, 3),
    ("diff: unreachable", _diff_unreachable, False, 4),
    ("diff: 401", _diff_answered(401), False, 4),
    ("diff: 403", _diff_answered(403), False, 4),
    ("diff: 500", _diff_answered(500), False, 4),
]


@BOTH
@pytest.mark.parametrize(("outcome", "reach", "terminal", "code"), OUTCOMES, ids=[outcome for outcome, *_ in OUTCOMES])
def test_each_outcome_exits_with_its_code(
    cli: _Cli,
    outcome: str,
    reach: Callable[[_Cli, Path], int],
    terminal: bool,
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: terminal)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    got = reach(cli, tmp_path)
    assert got == code, f"embry0 {cli} ({outcome}) must exit {code}, as every document CLI does; it exited {got}"


def _main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> object:
    """The exit status of ``embry0 <argv>``, through the real entry point and its argument parser."""
    import embry0.cli.__main__ as entry

    monkeypatch.setattr("sys.argv", ["embry0", *argv])
    with pytest.raises(SystemExit) as done:
        entry.main()
    return done.value.code


@BOTH
def test_a_usage_error_exits_2(cli: _Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    for argv in ([cli.command, "diff"], [cli.command, "no-such-verb"], [cli.command, "export", "--no-such-flag"]):
        got = _main(monkeypatch, *argv)
        assert got == 2, f"`embry0 {' '.join(argv)}` is a usage error and must exit 2; it exited {got}"


@BOTH
def test_an_unreachable_server_exits_4(cli: _Cli, monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as probe:  # a port nothing listens on once it is closed
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    got = _main(monkeypatch, cli.command, "export", "--api-url", f"http://127.0.0.1:{port}", "--api-key", "k")
    assert got == 4, f"`embry0 {cli} export` with no orchestrator listening must exit 4; it exited {got}"


@BOTH
def test_the_help_states_the_codes(
    cli: _Cli, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(monkeypatch, cli.command, "--help") == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Exit codes: 0 done, 1 refused, 2 usage, 3 conflict, 4 orchestrator unavailable." in help_text, (
        f"`embry0 {cli} --help` must state the exit codes"
    )


def test_both_commands_read_the_codes_from_one_module() -> None:
    """The numbers are written once, so the two commands cannot drift apart."""
    assert importlib.util.find_spec("embry0.cli.exit_codes") is not None, "the codes belong in embry0.cli.exit_codes"
    from embry0.cli import exit_codes

    assert {name: getattr(exit_codes, name) for name in SCHEME} == SCHEME
    for module in (settings, progression_pack):
        tree = ast.parse(inspect.getsource(module))
        imports = [
            node for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == exit_codes.__name__
        ]
        assigned = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        assert imports, f"{module.__name__} must import its exit codes from {exit_codes.__name__}"
        assert not assigned & SCHEME.keys(), (
            f"{module.__name__} defines exit codes of its own: {assigned & SCHEME.keys()}"
        )

"""`embry0 progression-pack` (#176): check offline with the API's validator; diff, apply and export through the API."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
import pytest

from embry0.cli.exit_codes import CONFLICT, REFUSED, UNAVAILABLE, USAGE
from embry0.cli.progression_pack import run
from embry0.services.progression_pack import PackInvalid, default_text
from embry0.services.progression_pack_store import read_text

DEFAULT = default_text()
COPPER = DEFAULT.replace("{id: bronze, name: Bronze,", "{id: bronze, name: Copper,")
#: Two problems at once: a level past the bounds and an unknown section.
BAD = COPPER.replace("max_level: 99", "max_level: 9999").replace("version: 1\n", "version: 1\nmystery: 1\n")
#: A key written twice, which YAML alone would silently keep the second of.
TWICE = COPPER + "\ntimezone: UTC\n"


def _args(action: str, **kw: Any) -> Namespace:
    return Namespace(action=action, **{"yes": False, "reason": "", "default": False, "output": None, **kw})


class _Api:
    """The orchestrator's pack routes, recorded; each answer is queued per method."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return self.answers.pop(0)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _file(tmp_path: Path, text: str) -> str:
    path = tmp_path / "pack.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ---- check, offline ----


def test_check_passes_the_shipped_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(_args("check", file=_file(tmp_path, DEFAULT))) == 0
    assert "the pack is valid" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "keys"), [(BAD, ["curve.max_level", "mystery"]), (TWICE, ["timezone"])], ids=["invalid", "twice"]
)
def test_check_exits_1_with_the_messages_a_write_is_refused_with(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, keys: list[str]
) -> None:
    with pytest.raises(PackInvalid) as refused:  # the API's own validator, as a write meets it
        read_text(text)
    expected = [str(p) for p in refused.value.problems]
    assert run(_args("check", file=_file(tmp_path, text))) == REFUSED
    printed = capsys.readouterr().err.splitlines()
    assert printed == expected, "check prints exactly the API's problems, one per line, as key: message"
    assert [line.split(":", 1)[0] for line in printed] == keys


def test_check_of_a_missing_or_oversized_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(_args("check", file=str(tmp_path / "nope.yaml"))) == USAGE
    assert run(_args("check", file=_file(tmp_path, "#" * (256 * 1024 + 1)))) == REFUSED
    assert "larger than 256 KiB" in capsys.readouterr().err


# ---- diff, apply, export through the API ----


def _preview(would_change: bool, version: int = 4) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "would_change": would_change,
            "version": version,
            "hash": "h",
            "sections": ["tiers"] if would_change else [],
            "text_diff": "--- active\n+++ proposed\n-Bronze\n+Copper\n" if would_change else "",
            "warnings": ["web:web trains no skill: its calls earn nothing"],
        },
    )


def test_diff_is_a_dry_run_and_prints_the_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_preview(True))
    assert run(_args("diff", file=_file(tmp_path, COPPER)), api_key="k", transport=api.transport) == 0
    (request,) = api.seen
    assert (request.method, request.url.path) == ("PUT", "/api/v1/progression/pack")
    assert dict(request.url.params) == {"dry_run": "1", "source": "file"}
    assert request.content == COPPER.encode() and request.headers["Content-Type"] == "application/yaml"
    assert request.headers["Authorization"] == "Bearer k"
    assert request.headers["X-Requested-With"] == "XMLHttpRequest"
    out = capsys.readouterr().out
    assert "would change: yes" in out and "sections: tiers" in out and "+Copper" in out and "warning: web:web" in out


def test_diff_prints_each_problem_of_a_422_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail = [
        {"loc": ["curve", "max_level"], "type": "less_than_equal", "msg": "Input should be less than or equal to 200"},
        {"loc": ["achievements", 3, "thresholds"], "type": "thresholds", "msg": "3 values"},
    ]
    api = _Api(httpx.Response(422, json={"detail": detail}))
    assert run(_args("diff", file=_file(tmp_path, BAD)), transport=api.transport) == REFUSED
    err = capsys.readouterr().err
    assert "curve.max_level: Input should be less than or equal to 200" in err
    assert "achievements[3].thresholds: 3 values" in err


def test_apply_of_the_pack_in_effect_sends_no_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_preview(False))
    assert run(_args("apply", file=_file(tmp_path, DEFAULT), yes=True), transport=api.transport) == 0
    assert [dict(r.url.params) for r in api.seen] == [{"dry_run": "1", "source": "file"}], (
        "an unchanged file must make no version: only the dry run is sent"
    )
    assert "nothing changes" in capsys.readouterr().out


def test_apply_shows_the_diff_then_activates_against_the_version_it_diffed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    activated = httpx.Response(
        200, json={"changed": True, "version": 5, "previous_version": 4, "source": "file", "warnings": []}
    )
    api = _Api(_preview(True, version=4), activated)
    args = _args("apply", file=_file(tmp_path, COPPER), yes=True, reason="rename the first tier")
    assert run(args, transport=api.transport) == 0
    dry, write = api.seen
    assert dict(dry.url.params)["dry_run"] == "1"
    assert dict(write.url.params) == {"source": "file", "reason": "rename the first tier"}
    assert write.headers["If-Match"] == '"4"', "a pack changed since the diff must be refused, not overwritten"
    assert write.content == COPPER.encode()
    out = capsys.readouterr().out
    assert out.index("+Copper") < out.index("activated version 5 (it replaces version 4)")


def test_apply_refuses_without_a_terminal_or_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    api = _Api(_preview(True))
    assert run(_args("apply", file=_file(tmp_path, COPPER)), transport=api.transport) == USAGE
    assert len(api.seen) == 1, "only the dry run went out"


def test_apply_reports_a_pack_changed_since_the_diff(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conflict = httpx.Response(409, json={"detail": "The active pack changed since you read it (now version 6)."})
    api = _Api(_preview(True), conflict)
    assert run(_args("apply", file=_file(tmp_path, COPPER), yes=True), transport=api.transport) == CONFLICT
    assert "now version 6" in capsys.readouterr().err


def test_export_writes_the_shipped_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(httpx.Response(200, text=DEFAULT, headers={"Content-Type": "application/yaml"}))
    out = tmp_path / "default.yaml"
    assert run(_args("export", default=True, output=str(out)), transport=api.transport) == 0
    (request,) = api.seen
    assert (request.method, request.url.path, dict(request.url.params)) == (
        "GET",
        "/api/v1/progression/pack/default",
        {"format": "yaml"},
    )
    assert out.read_text(encoding="utf-8") == DEFAULT
    api = _Api(httpx.Response(200, text=COPPER))
    assert run(_args("export"), transport=api.transport) == 0
    assert api.seen[0].url.path == "/api/v1/progression/pack"
    assert capsys.readouterr().out == COPPER


def test_export_says_when_the_stored_pack_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """With a stored version this release refuses, the export is the shipped default in effect, and says so."""
    api = _Api(httpx.Response(200, text=DEFAULT, headers={"X-Embry0-Pack-Refused": "4"}))
    assert run(_args("export"), transport=api.transport) == 0
    captured = capsys.readouterr()
    assert captured.out == DEFAULT
    assert "the stored version 4 is refused" in captured.err, "the export must say it is not the stored text"


def test_an_unreachable_orchestrator_or_a_bad_key_is_unavailable(tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert run(_args("export"), transport=httpx.MockTransport(down)) == UNAVAILABLE
    api = _Api(httpx.Response(401, json={"detail": "bad key"}))
    assert run(_args("diff", file=_file(tmp_path, COPPER)), transport=api.transport) == UNAVAILABLE


def test_the_subcommand_is_wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import embry0.cli.__main__ as cli

    monkeypatch.setattr("sys.argv", ["embry0", "progression-pack", "check", _file(tmp_path, DEFAULT)])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0

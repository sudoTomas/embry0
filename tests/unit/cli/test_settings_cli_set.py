"""`embry0 settings set SECTION KEY=VALUE...`: change some keys of one section through the API.

The API is faked at the transport, so each test sees exactly what the
command sends (method, path, query, headers, body) and the exit code it
answers with: 0 done, 1 refused, 2 usage, 3 conflict, 4 unavailable.
"""

from __future__ import annotations

import json
from argparse import Namespace
from typing import Any

import httpx
import pytest

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED, UNAVAILABLE, USAGE
from embry0.cli.settings import run

URL = "/api/v1/config/settings"


def _args(section: str, *values: str, **kw: Any) -> Namespace:
    defaults = {"yes": True, "reason": "", "org": None, "repo": None}
    return Namespace(action="set", section=section, values=list(values), **{**defaults, **kw})


class _Api:
    """The orchestrator's settings routes, recorded; answers are given in order."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if not self.answers:
            return httpx.Response(599, json={"detail": "the test queued no answer for this request"})
        return self.answers.pop(0)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def sent(self) -> list[tuple[str, str, dict[str, str]]]:
        return [(r.method, r.url.path, dict(r.url.params)) for r in self.seen]


def _stored(generation: int = 7) -> httpx.Response:
    return httpx.Response(200, json={"generation": generation}, headers={"ETag": f'"{generation}"'})


def _preview(would_change: bool = True, managed_by: str | None = None) -> httpx.Response:
    change = {
        "key": "asks.remind_hours",
        "before": None,
        "after": 12,
        "set_before": False,
        "set_after": True,
        "effective_before": 24.0,
        "effective_after": 12.0,
    }
    return httpx.Response(
        200,
        json={
            "would_change": would_change,
            "text_diff": "--- stored\n+++ proposed\n@@ -1 +1,2 @@\n asks:\n+  remind_hours: 12\n"
            if would_change
            else "",
            "changes": [change] if would_change else [],
            "unknown_sections": [],
            "unknown_keys": [],
            "managed_by": managed_by,
        },
    )


def _written() -> httpx.Response:
    return httpx.Response(200, json={"generation": 8, "revision_id": 12, "changes": [], "managed_by": None})


def test_set_shows_the_change_then_writes_it_against_the_generation_it_read(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_stored(7), _preview(), _written())
    args = _args(
        "asks", "remind_hours=12", "enabled=true", "hours=08-17", 'weekdays=["mon", "tue"]', reason="busy week"
    )
    assert run(args, transport=api.transport) == OK
    assert api.sent() == [
        ("GET", URL, {}),
        ("PATCH", f"{URL}/asks", {"dry_run": "1"}),
        ("PATCH", f"{URL}/asks", {"reason": "busy week"}),
    ], "set must read the generation, show the dry run, then write"
    read, dry, write = api.seen
    for request in (dry, write):
        assert request.headers.get("If-Match") == '"7"', "the change must be refused over a newer document"
        assert request.headers["Content-Type"] == "application/json"
        assert json.loads(request.content) == {
            "remind_hours": 12,
            "enabled": True,
            "hours": "08-17",
            "weekdays": ["mon", "tue"],
        }, "a value is JSON when it is JSON (a number, a switch, a list), else text as written"
    out = capsys.readouterr().out
    assert "  asks.remind_hours: 24.0 (default) -> 12" in out
    assert out.index("+  remind_hours: 12") < out.index("changed: generation 8, revision 12")


def test_null_takes_a_key_out() -> None:
    api = _Api(_stored(), _preview(), _written())
    assert run(_args("asks", "remind_hours=null", "hours= null "), transport=api.transport) == OK
    assert json.loads(api.seen[2].content) == {"remind_hours": None, "hours": None}


@pytest.mark.parametrize(
    ("raw", "sent"),
    [
        ("12", 12),
        ("-3", -3),
        ("2.5", 2.5),
        ("1e3", 1000.0),
        ("true", True),
        ("false", False),
        ('["a", "b c"]', ["a", "b c"]),
        ('{"a": 1}', {"a": 1}),
        ('"010"', "010"),
        ('"1:30"', "1:30"),
        ('"null"', "null"),
        ("08-17", "08-17"),
        ("Europe/Berlin", "Europe/Berlin"),
        ("2026-01-02", "2026-01-02"),
        ("0x10", "0x10"),
        ("yes", "yes"),
        ("on", "on"),
        ("Off", "Off"),
        ("~", "~"),
        ("Null", "Null"),
        ("+5", "+5"),
    ],
)
def test_a_value_is_json_when_it_is_json_and_else_the_text_as_written(raw: str, sent: object) -> None:
    """YAML 1.1 would read ``0x10`` as 16, ``yes``/``on``/``Off`` as switches and ``~``/``Null`` as null: here
    they are text, which a section refuses where it wants a number or a switch, never another value."""
    api = _Api(_stored(), _preview(would_change=False))
    assert run(_args("sample", f"value={raw}"), transport=api.transport) == OK
    body = json.loads(api.seen[1].content)
    assert body == {"value": sent} and type(body["value"]) is type(sent), (
        f"value={raw} was sent as {body['value']!r}, not {sent!r}"
    )


def test_a_change_of_nothing_sends_no_write(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_stored(), _preview(would_change=False))
    assert run(_args("asks", "remind_hours=24"), transport=api.transport) == OK
    assert [r.method for r in api.seen] == ["GET", "PATCH"], "nothing to change: only the read and the dry run"
    assert "no revision is recorded" in capsys.readouterr().out


def test_a_layer_is_named_in_every_request() -> None:
    api = _Api(_stored(0), _preview(), _written())
    assert run(_args("asks", "hours='08-17'", org="acme"), transport=api.transport) == OK
    assert [dict(r.url.params) for r in api.seen] == [
        {"owner": "acme"},
        {"dry_run": "1", "owner": "acme"},
        {"owner": "acme"},
    ]
    api = _Api(_stored(0), _preview(), _written())
    assert run(_args("sample", "mode=on", repo="acme/widgets"), transport=api.transport) == OK
    assert {r.url.params.get("repo") for r in api.seen} == {"acme/widgets"}


def test_a_section_name_is_one_path_segment() -> None:
    api = _Api(
        _stored(), httpx.Response(422, json={"detail": [{"loc": ["a/b"], "type": "unknown_section", "msg": "no"}]})
    )
    assert run(_args("a/b", "x=1"), transport=api.transport) == REFUSED
    assert api.seen[1].url.raw_path.startswith(b"/api/v1/config/settings/a%2Fb?"), api.seen[1].url.raw_path


def test_a_document_kept_in_a_file_is_named_before_the_change(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_stored(), _preview(managed_by="file"), _written())
    assert run(_args("asks", "remind_hours=12"), transport=api.transport) == OK
    assert "kept in a file (managed_by: file): this change lasts until that file is applied again" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["remind_hours"], "argument 1: write KEY=VALUE, or KEY=null to take the key out"),
        (["=12"], "argument 1: write KEY=VALUE"),
        (["remind_hours="], "remind_hours: give it a value, or write remind_hours=null to take it out"),
        (["hours=1", "hours=2"], "hours: given twice"),
        (["per_tick=010"], "per_tick: a number written with a leading zero or a colon could be meant as text"),
        (["per_tick=-007"], "per_tick: a number written with a leading zero or a colon"),
        (["per_tick=00.5"], "per_tick: a number written with a leading zero or a colon"),
        (["wait=1:30"], "wait: a number written with a leading zero or a colon"),
        (["at=12:30:00"], "at: a number written with a leading zero or a colon"),
        (["map={a: 1, a: 2}"], """map: write a list or a mapping as JSON, such as map='["a", "b"]'"""),
        (["labels=[a, b]"], "labels: write a list or a mapping as JSON"),
        (['map={"a": 1, "a": 2}'], "map: the value writes a key twice"),
        (["x=NaN"], "x: use a finite number"),
        (["x=[-Infinity]"], "x: use a finite number"),
    ],
    ids=[
        "no equals sign",
        "no key",
        "no value",
        "a key twice",
        "a leading zero",
        "a negative leading zero",
        "a leading zero before a point",
        "minutes and seconds",
        "hours, minutes and seconds",
        "a mapping that is not JSON",
        "a list that is not JSON",
        "a JSON key twice",
        "not a number",
        "an infinity in a list",
    ],
)
def test_a_bad_assignment_is_a_usage_error_and_sends_nothing(
    values: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api()
    assert run(_args("asks", *values), transport=api.transport) == USAGE, f"{values} was not refused"
    assert api.seen == [], "a bad argument must stop the command before anything is sent"
    err = capsys.readouterr().err
    assert message in err, err


def test_a_number_with_a_leading_zero_is_never_stored_as_another_number() -> None:
    """``set sample per_tick=010 --yes`` used to store 8 (YAML 1.1 octal) without a word."""
    api = _Api(_stored(), _preview(), _written())
    code = run(_args("sample", "per_tick=010"), transport=api.transport)
    assert api.seen == [], f"per_tick=010 was sent: {[r.content for r in api.seen]}"
    assert code == USAGE, f"per_tick=010 was not refused (exit {code})"


def test_a_refused_change_prints_each_problem_by_key_and_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    detail = [
        {"loc": ["asks", "remind_hours"], "type": "greater_than", "msg": "Input should be greater than 0", "input": -1}
    ]
    api = _Api(_stored(), httpx.Response(422, json={"detail": detail}))
    assert run(_args("asks", "remind_hours=-1"), transport=api.transport) == REFUSED
    assert len(api.seen) == 2, "nothing is written after a refused dry run"
    assert "  asks.remind_hours: Input should be greater than 0" in capsys.readouterr().err


def test_a_change_over_a_newer_document_is_a_conflict() -> None:
    conflict = httpx.Response(409, json={"detail": "The settings changed since you read them (now generation 9)."})
    api = _Api(_stored(7), _preview(), conflict)
    assert run(_args("asks", "remind_hours=12"), transport=api.transport) == CONFLICT


def test_set_asks_at_a_terminal_and_refuses_without_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    api = _Api(_stored(), _preview())
    assert run(_args("asks", "remind_hours=12", yes=False), transport=api.transport) == USAGE
    assert [r.method for r in api.seen] == ["GET", "PATCH"], "only the read and the dry run went out"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "n")
    api = _Api(_stored(7), _preview())
    assert run(_args("asks", "remind_hours=12", yes=False), transport=api.transport) == REFUSED
    assert asked == ["Store this change over generation 7? [y/N] "]
    assert len(api.seen) == 2, "a change declined at the prompt writes nothing"


def test_the_reason_is_checked_before_anything_is_sent(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api()
    assert run(_args("asks", "remind_hours=12", reason="x" * 501), transport=api.transport) == USAGE
    assert api.seen == []


def test_an_orchestrator_that_refuses_the_key_is_unavailable() -> None:
    api = _Api(httpx.Response(403, json={"detail": "admin only"}))
    assert run(_args("asks", "remind_hours=12"), transport=api.transport) == UNAVAILABLE


def test_the_verb_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    import embry0.cli.__main__ as cli

    seen: dict[str, Any] = {}

    def fake_run(args: Namespace, **kw: Any) -> int:
        seen.update(vars(args))
        return OK

    monkeypatch.setattr("embry0.cli.settings.run", fake_run)
    monkeypatch.setattr("sys.argv", ["embry0", "settings", "set", "asks", "remind_hours=12", "--org", "acme", "--yes"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == OK
    assert (seen["action"], seen["section"], seen["values"], seen["org"], seen["yes"]) == (
        "set",
        "asks",
        ["remind_hours=12"],
        "acme",
        True,
    )
    monkeypatch.setattr("sys.argv", ["embry0", "settings", "set", "asks"])
    with pytest.raises(SystemExit) as usage:
        cli.main()
    assert usage.value.code == USAGE, "set needs at least one KEY=VALUE"

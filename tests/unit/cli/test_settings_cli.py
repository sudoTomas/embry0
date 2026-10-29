"""`embry0 settings` (#162): check a file offline; diff, apply, export, list and undo through the API.

The API is faked at the transport, so each test sees exactly what the
command sends (method, path, query, headers, body) and the exit code it
answers with: 0 done, 1 refused, 2 usage, 3 conflict, 4 unavailable.
"""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED, UNAVAILABLE, USAGE
from embry0.cli.settings import problem_line, run
from embry0.settings.document import DocumentInvalid, check, load_yaml
from embry0.settings.example import render
from embry0.settings.sections import CATALOG
from tests.fixtures.settings_sections import TEST_CATALOG

GOOD = "# office hours\nasks:\n  enabled: true\n  hours: '08-17'\n"
#: An unknown section, a wrong type, a value out of range and a zone nobody knows, at once.
BAD = "aks:\n  x: 1\nasks:\n  enabled: maybe\n  remind_hours: 0\n  timezone: Mars/Base\n"
URL = "/api/v1/config/settings"


def _args(action: str, **kw: Any) -> Namespace:
    defaults = {
        "yes": False,
        "allow_unset": False,
        "reason": "",
        "default": False,
        "output": None,
        "id": None,
        "limit": 20,
    }
    return Namespace(action=action, **{**defaults, **kw})


class _Api:
    """The orchestrator's settings routes, recorded; answers are given in order."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if not self.answers:  # a request the test did not expect: recorded, and answered as a failure
            return httpx.Response(599, json={"detail": "the test queued no answer for this request"})
        return self.answers.pop(0)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def sent(self) -> list[tuple[str, str, dict[str, str]]]:
        return [(r.method, r.url.path, dict(r.url.params)) for r in self.seen]


def _file(tmp_path: Path, text: str, name: str = "settings.yaml") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _stored(generation: int = 7) -> httpx.Response:
    return httpx.Response(200, json={"yaml_text": "", "generation": generation}, headers={"ETag": f'"{generation}"'})


def _preview(would_change: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "would_change": would_change,
            "text_diff": "--- stored\n+++ proposed\n@@ -0,0 +1 @@\n+asks:\n" if would_change else "",
            "changes": [
                {
                    "key": "asks.hours",
                    "before": None,
                    "after": "08-17",
                    "set_before": False,
                    "set_after": True,
                    "effective_before": "09-18",
                    "effective_after": "08-17",
                }
            ]
            if would_change
            else [],
            "unknown_sections": [],
            "unknown_keys": [],
        },
    )


def _api_problems(text: str) -> list[dict[str, Any]]:
    """What the API's 422 carries for ``text``: the same check, as ``PUT /config/settings`` renders it."""
    with pytest.raises(DocumentInvalid) as refused:
        check(CATALOG, load_yaml(text), stored={})
    return [problem.to_dict() for problem in refused.value.problems]


# ---- check, offline ----


def test_check_passes_the_example_and_names_what_a_file_sets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(_args("check", file=_file(tmp_path, render(CATALOG)))) == OK
    assert "valid; it sets nothing" in capsys.readouterr().out
    assert run(_args("check", file=_file(tmp_path, "managed_by: file\n" + GOOD))) == OK
    assert "it sets managed_by, asks.enabled, asks.hours" in capsys.readouterr().out


def test_check_exits_1_with_the_api_s_messages_by_key_never_the_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = [problem_line(p["loc"], p["msg"]) for p in _api_problems(BAD)]
    assert run(_args("check", file=_file(tmp_path, BAD))) == REFUSED
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "the settings document has problems:"
    assert [line.strip() for line in err[1:]] == expected, "check prints exactly the API's problems, one per line"
    assert {line.split(":", 1)[0].strip() for line in err[1:]} == {
        "aks",
        "asks.enabled",
        "asks.remind_hours",
        "asks.timezone",
    }
    for value in ("maybe", "Mars/Base"):
        assert value not in "\n".join(err), f"check echoed the rejected value {value!r}"


def test_check_of_a_file_that_cannot_be_read_or_would_be_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(_args("check", file=str(tmp_path / "missing.yaml"))) == USAGE
    assert run(_args("check", file=_file(tmp_path, "#" * (256 * 1024 + 1)))) == REFUSED
    assert "larger than 256 KiB" in capsys.readouterr().err
    latin = tmp_path / "latin.yaml"
    latin.write_bytes("# caf\xe9\n".encode("latin-1"))
    assert run(_args("check", file=str(latin))) == REFUSED
    assert "UTF-8" in capsys.readouterr().err
    assert run(_args("check", file=_file(tmp_path, "asks: [\n"))) == REFUSED
    assert "The YAML cannot be read: line" in capsys.readouterr().err


def test_check_reads_a_json_file_as_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Tab-indented JSON is valid JSON and not valid YAML: a *.json file must be read as JSON."""
    text = '{\n\t"asks": {\n\t\t"enabled": true\n\t}\n}\n'
    assert run(_args("check", file=_file(tmp_path, text, "settings.json"))) == OK, capsys.readouterr().err
    assert "it sets asks.enabled" in capsys.readouterr().out
    assert run(_args("check", file=_file(tmp_path, text, "settings.yaml"))) == REFUSED, "the same text as YAML"


# ---- diff and apply ----


def test_diff_is_a_dry_run_of_the_file_as_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(_preview())
    assert run(_args("diff", file=_file(tmp_path, GOOD)), api_key="k", transport=api.transport) == OK
    (request,) = api.seen
    assert (request.method, request.url.path, dict(request.url.params)) == (
        "PUT",
        URL,
        {"dry_run": "1", "source": "file"},
    )
    assert request.content == GOOD.encode(), "the file goes as written, comments kept"
    assert request.headers["Content-Type"] == "application/yaml"
    assert (request.headers["Authorization"], request.headers["X-Requested-With"]) == ("Bearer k", "XMLHttpRequest")
    assert "If-Match" not in request.headers
    out = capsys.readouterr().out
    assert "would change: yes" in out
    assert '  asks.hours: "09-18" (default) -> "08-17"' in out
    assert "+asks:" in out


def test_a_422_lists_each_problem_by_key_and_never_its_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    detail = [
        {"loc": ["asks", "remind_hours"], "type": "greater_than", "msg": "Input should be greater than 0"},
        {"loc": [], "type": "yaml_syntax", "msg": "The YAML cannot be read: line 3, column 1: oops"},
        {"loc": ["asks", "hours"], "type": "value_error", "msg": "Value error, bad hours", "input": "s3cret-ish"},
    ]
    api = _Api(httpx.Response(422, json={"detail": detail}))
    assert run(_args("diff", file=_file(tmp_path, GOOD)), transport=api.transport) == REFUSED
    err = capsys.readouterr().err
    assert "  asks.remind_hours: Input should be greater than 0\n" in err
    assert "  The YAML cannot be read: line 3, column 1: oops\n" in err
    assert "  asks.hours: bad hours\n" in err
    assert "s3cret-ish" not in err, "a value the API echoed in input must not be printed"


def test_apply_of_the_stored_text_sends_no_write_and_records_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api(_stored(), _preview(would_change=False))
    assert run(_args("apply", file=_file(tmp_path, GOOD), yes=True), transport=api.transport) == OK
    assert api.sent() == [("GET", URL, {}), ("PUT", URL, {"dry_run": "1", "source": "file"})], (
        "an unchanged file must create no revision: only the read and the dry run are sent"
    )
    assert "no revision is recorded" in capsys.readouterr().out


def test_apply_shows_the_diff_then_writes_against_the_generation_it_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    written = httpx.Response(200, json={"generation": 8, "revision_id": 12, "changes": []})
    api = _Api(_stored(7), _preview(), written)
    args = _args("apply", file=_file(tmp_path, GOOD), yes=True, reason="office hours")
    assert run(args, transport=api.transport) == OK
    read, dry, write = api.seen
    assert (read.method, read.url.path) == ("GET", URL)
    assert dry.headers.get("If-Match") == '"7"', "the diff must be of the generation the write will name"
    assert (write.method, dict(write.url.params)) == ("PUT", {"source": "file", "reason": "office hours"})
    assert write.headers.get("If-Match") == '"7"', "a document changed since the read must be refused, not overwritten"
    assert write.content == GOOD.encode() and write.headers["Content-Type"] == "application/yaml"
    out = capsys.readouterr().out
    assert out.index("+asks:") < out.index("applied: generation 8, revision 12")


def test_diff_and_apply_send_a_json_file_as_json(tmp_path: Path) -> None:
    text = '{\n\t"asks": {"enabled": true}\n}\n'
    path = _file(tmp_path, text, "settings.json")
    api = _Api(_preview())
    assert run(_args("diff", file=path), transport=api.transport) == OK
    written = httpx.Response(200, json={"generation": 8, "revision_id": 12, "changes": []})
    api = _Api(_stored(7), _preview(), written)
    assert run(_args("apply", file=path, yes=True), transport=api.transport) == OK
    _, dry, write = api.seen
    for request in (dry, write):
        assert request.headers["Content-Type"] == "application/json", "a *.json file is sent as JSON, not YAML"
        assert request.content == text.encode()


def _unsetting(would_change: bool = True) -> httpx.Response:
    """A dry run whose file leaves out a key that is set now (an imported one, say)."""
    return httpx.Response(
        200,
        json={
            "would_change": would_change,
            "text_diff": "--- stored\n+++ proposed\n@@ -1,2 +1 @@\n asks:\n-  enabled: false\n",
            "changes": [
                {
                    "key": "asks.enabled",
                    "before": False,
                    "after": None,
                    "set_before": True,
                    "set_after": False,
                    "effective_before": False,
                    "effective_after": False,
                }
            ],
            "unknown_sections": [],
            "unknown_keys": [],
        },
    )


def test_apply_yes_refuses_to_unset_a_key_without_allow_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api(_stored(7), _unsetting())
    assert run(_args("apply", file=_file(tmp_path, "asks: {}\n"), yes=True), transport=api.transport) == USAGE
    assert [r.method for r in api.seen] == ["GET", "PUT"], "nothing is written: only the read and the dry run"
    captured = capsys.readouterr()
    assert "unsets (set now, left out of the file, so back to the default):" in captured.out
    assert "  asks.enabled: false -> false (default)" in captured.out
    assert "refusing to unset asks.enabled without --allow-unset" in captured.err


def test_apply_unsets_a_key_with_allow_unset_or_at_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    written = httpx.Response(200, json={"generation": 8, "revision_id": 12, "changes": []})
    api = _Api(_stored(7), _unsetting(), written)
    args = _args("apply", file=_file(tmp_path, "asks: {}\n"), yes=True, allow_unset=True)
    assert run(args, transport=api.transport) == OK
    assert [r.method for r in api.seen] == ["GET", "PUT", "PUT"]
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "y")
    api = _Api(_stored(7), _unsetting(), written)
    assert run(_args("apply", file=_file(tmp_path, "asks: {}\n")), transport=api.transport) == OK
    assert asked == ["Store this document over generation 7, unsetting 1 key(s)? [y/N] "], (
        "a person at the prompt is told the file unsets a key before answering"
    )


def test_apply_checks_the_reason_before_anything_is_sent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api()
    args = _args("apply", file=_file(tmp_path, GOOD), yes=True, reason="x" * 501)
    assert run(args, transport=api.transport) == USAGE
    assert api.seen == [], "a reason the API would refuse must stop the command before it asks or writes"
    assert "--reason is longer than 500 characters" in capsys.readouterr().err


def test_apply_refuses_without_a_terminal_or_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    api = _Api(_stored(), _preview())
    assert run(_args("apply", file=_file(tmp_path, GOOD)), transport=api.transport) == USAGE
    assert [r.method for r in api.seen] == ["GET", "PUT"], "only the read and the dry run went out"


def test_apply_declined_at_the_prompt_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    api = _Api(_stored(), _preview())
    assert run(_args("apply", file=_file(tmp_path, GOOD)), transport=api.transport) == REFUSED
    assert len(api.seen) == 2


def test_apply_over_a_newer_document_is_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    conflict = httpx.Response(409, json={"detail": "The settings changed since you read them (now generation 9)."})
    api = _Api(_stored(7), _preview(), conflict)
    assert run(_args("apply", file=_file(tmp_path, GOOD), yes=True), transport=api.transport) == CONFLICT
    assert "now generation 9" in capsys.readouterr().err


def test_apply_of_a_refused_document_stops_at_the_dry_run(tmp_path: Path) -> None:
    api = _Api(_stored(), httpx.Response(422, json={"detail": _api_problems(BAD)}))
    assert run(_args("apply", file=_file(tmp_path, BAD), yes=True), transport=api.transport) == REFUSED
    assert len(api.seen) == 2, "nothing is written after a refused dry run"


# ---- export and import-env ----


def test_export_writes_the_stored_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(httpx.Response(200, text=GOOD, headers={"Content-Type": "application/yaml"}))
    out = tmp_path / "stored.yaml"
    assert run(_args("export", output=str(out)), transport=api.transport) == OK
    assert api.sent() == [("GET", URL, {"format": "yaml"})]
    assert out.read_text(encoding="utf-8") == GOOD
    api = _Api(httpx.Response(200, text=""))
    assert run(_args("export"), transport=api.transport) == OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "every key takes its default" in captured.err


def test_export_default_is_the_example_and_needs_no_orchestrator(capsys: pytest.CaptureFixture[str]) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise AssertionError("export --default must not call the orchestrator")

    assert run(_args("export", default=True), transport=httpx.MockTransport(down)) == OK
    assert capsys.readouterr().out == render(CATALOG)


def test_import_env_writes_the_variables_set_and_leaves_out_a_refused_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)  # no ./.env is read
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    env = {"ASKS_ENABLED": "true", "SCOUT_REPOS": "acme/widgets, acme/gears", "ASK_TIMEZONE": "Mars/Base"}
    with patch.dict(os.environ, env, clear=True):
        code = run(_args("import-env"))
    captured = capsys.readouterr()
    assert code == REFUSED, "a refused variable must fail a script, though the rest is written"
    document = load_yaml(captured.out)
    assert document == {"version": 1, "sample": {"enabled": True, "labels": ["acme/widgets", "acme/gears"]}}
    assert "ASKS_ENABLED, SCOUT_REPOS" in captured.out, "the text names the variables it read"
    assert "ASK_TIMEZONE (sample.zone) is refused" in captured.err
    assert "Mars/Base" not in captured.err, "a refused value is never echoed"
    check(TEST_CATALOG, document, stored={})


def test_import_env_of_an_environment_at_its_defaults_sets_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("embry0.settings.sections.CATALOG", TEST_CATALOG)
    with patch.dict(os.environ, {"ASKS_ENABLED": "false"}, clear=True):
        assert run(_args("import-env", output=str(tmp_path / "env.yaml"))) == OK
    assert load_yaml((tmp_path / "env.yaml").read_text(encoding="utf-8")) == {"version": 1}
    assert "sets nothing" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value",
    ["http://oauth2:tok-000000000007@gitlab.example.com", "http://tok-000000000007@gitlab.example.com"],
    ids=["password", "bare-user"],
)
def test_import_env_never_prints_a_token_a_refused_url_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {"GITLAB_URL": value}, clear=True):
        assert run(_args("import-env")) == REFUSED
    captured = capsys.readouterr()
    assert "\n  GITLAB_URL must be an https:// URL" in captured.err, captured.err
    assert "tok-000000000007" not in captured.err + captured.out, "the token in the refused URL was printed"


def test_import_env_of_an_environment_that_does_not_load_names_the_variable_not_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {"ASK_REMIND_HOURS": "s3cret-ish"}, clear=True):
        assert run(_args("import-env")) == REFUSED
    captured = capsys.readouterr()
    assert "ASK_REMIND_HOURS: Input should be a valid number" in captured.err
    assert "s3cret-ish" not in captured.err + captured.out, "a value the environment holds is never echoed"


# ---- history and undo ----


_REVISION = {
    "id": 12,
    "what": "Changed: asks.hours",
    "actor": "alex@example.com",
    "source": "file",
    "reason": "office hours",
    "created_at": "2026-10-29T10:00:00+00:00",
    "undoable": True,
    "why_not": None,
    "before_text": "",
    "after_text": GOOD,
    "changes": [
        {"key": "asks.hours", "before": None, "after": "08-17", "set_before": False, "set_after": True},
    ],
}


def test_revisions_lists_the_history_and_names_the_undoable_change(capsys: pytest.CaptureFixture[str]) -> None:
    older = {**_REVISION, "id": 11, "undoable": False, "why_not": "This changed again later.", "reason": ""}
    api = _Api(httpx.Response(200, json={"revisions": [_REVISION, older]}))
    assert run(_args("revisions", limit=5), transport=api.transport) == OK
    assert api.sent() == [("GET", f"{URL}/revisions", {"limit": "5"})]
    out = capsys.readouterr().out
    assert "#12  2026-10-29T10:00:00+00:00  file  alex@example.com" in out
    assert "reason: office hours" in out
    assert "`embry0 settings undo 12`" in out and "`embry0 settings undo 11`" not in out


def test_one_revision_shows_its_changes_and_its_text_diff(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api(httpx.Response(200, json=_REVISION))
    assert run(_args("revisions", id=12), transport=api.transport) == OK
    assert api.sent() == [("GET", f"{URL}/revisions/12", {})]
    out = capsys.readouterr().out
    assert '  asks.hours: not set -> "08-17"' in out
    assert "+++ after #12" in out and "+  hours: '08-17'" in out


def test_undo_shows_what_it_puts_back_then_undoes(capsys: pytest.CaptureFixture[str]) -> None:
    undone = httpx.Response(200, json={"generation": 9, "revision_id": 13, "changes": []})
    api = _Api(httpx.Response(200, json=_REVISION), undone)
    assert run(_args("undo", id=12, yes=True), transport=api.transport) == OK
    assert api.sent() == [("GET", f"{URL}/revisions/12", {}), ("POST", f"{URL}/revisions/12/undo", {})]
    out = capsys.readouterr().out
    assert '  asks.hours: "08-17" -> not set' in out, "undo shows the change read backwards"
    assert "undone: generation 9, recorded as revision 13" in out


def test_undo_of_a_change_of_comments_only_shows_the_text_it_puts_back(capsys: pytest.CaptureFixture[str]) -> None:
    comments = {
        **_REVISION,
        "what": "Comments or layout changed; no setting changed",
        "before_text": "# office hours\nasks:\n  hours: '08-17'\n",
        "after_text": "# hours agreed with the team\nasks:\n  hours: '08-17'\n",
        "changes": [],
    }
    undone = httpx.Response(200, json={"generation": 9, "revision_id": 13, "changes": []})
    api = _Api(httpx.Response(200, json=comments), undone)
    assert run(_args("undo", id=12, yes=True), transport=api.transport) == OK
    out = capsys.readouterr().out
    assert "-# hours agreed with the team" in out and "+# office hours" in out, (
        "an undo that changes no setting must still show the text it puts back before it is confirmed"
    )
    assert out.index("+# office hours") < out.index("undone:")


def test_undo_of_an_older_change_is_a_conflict_and_sends_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    older = {**_REVISION, "undoable": False, "why_not": "This changed again later. Undo the newer change first."}
    api = _Api(httpx.Response(200, json=older))
    assert run(_args("undo", id=12, yes=True), transport=api.transport) == CONFLICT
    assert [r.method for r in api.seen] == ["GET"]
    assert "Undo the newer change first" in capsys.readouterr().err


@pytest.mark.parametrize(("status", "code"), [(409, CONFLICT), (422, REFUSED)])
def test_undo_refused_by_the_orchestrator(status: int, code: int) -> None:
    detail: Any = "This changed again later. Undo the newer change first."
    if status == 422:
        detail = [{"loc": [], "type": "revision_invalid", "msg": "That earlier version is no longer valid."}]
    api = _Api(httpx.Response(200, json=_REVISION), httpx.Response(status, json={"detail": detail}))
    assert run(_args("undo", id=12, yes=True), transport=api.transport) == code


def test_undo_refuses_without_a_terminal_or_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    api = _Api(httpx.Response(200, json=_REVISION))
    assert run(_args("undo", id=12), transport=api.transport) == USAGE
    assert [r.method for r in api.seen] == ["GET"]


# ---- the orchestrator ----


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_a_refused_key_or_a_failing_orchestrator_is_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    api = _Api(httpx.Response(status, json={"detail": "no"}))
    assert run(_args("diff", file=_file(tmp_path, GOOD)), transport=api.transport) == UNAVAILABLE
    assert str(status) in capsys.readouterr().err


def test_an_unreachable_orchestrator_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert run(_args("export"), transport=httpx.MockTransport(down)) == UNAVAILABLE


def test_the_subcommand_is_wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import embry0.cli.__main__ as cli

    monkeypatch.setattr("sys.argv", ["embry0", "settings", "check", _file(tmp_path, GOOD)])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == OK
    monkeypatch.setattr("sys.argv", ["embry0", "settings", "revisions", "--limit", "500"])
    with pytest.raises(SystemExit) as usage:
        cli.main()
    assert usage.value.code == USAGE

"""embry0 people check|diff|apply|export (#179 pull request 3): an upsert from a file, guarded, deleting nobody."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED

try:
    from embry0.cli.people import export_document, run
except ImportError:
    pytest.fail("there is no embry0 people command", pytrace=False)

ADA = {
    "id": "per-ada000000001",
    "updated_at": "2026-11-25T09:00:00+00:00",
    "name": "Ada",
    "email": "ada@example.com",
    "user_id": None,
    "timezone": None,
    "hours": "09-17",
    "weekdays": None,
    "disabled": False,
    "identities": [{"kind": "github", "value": "ada-l"}],
}
BO = {**ADA, "id": "per-bo0000000001", "name": "Bo", "email": "bo@example.com", "identities": []}


def _args(action: str, **kw: Any) -> Namespace:
    return Namespace(action=action, **{"yes": True, "output": None, **kw})


class _Orchestrator:
    """The people API as the command meets it: the people stored, and every request it was sent."""

    def __init__(self, *people: dict[str, Any], answer: int = 200, answers: list[int] | None = None) -> None:
        self.people = list(people)
        self.sent: list[tuple[str, str, Any, dict[str, str]]] = []
        self.answer = answer
        #: Each write's status in turn, when given; ``answer`` after them.
        self.answers = list(answers or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.sent.append((request.method, request.url.path, body, dict(request.headers)))
        if request.method == "GET":
            return httpx.Response(200, json={"people": self.people, "count": len(self.people)})
        status = self.answers.pop(0) if self.answers else self.answer
        if status != 200:
            return httpx.Response(status, json={"detail": "the person changed since it was read: read it again"})
        if request.method == "POST":
            return httpx.Response(201, json={**(body or {}), "id": "per-new000000001"})
        return httpx.Response(200, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def _file(tmp_path: Path, people: list[dict[str, Any]]) -> str:
    path = tmp_path / "people.yaml"
    path.write_text(yaml.safe_dump({"people": people}, sort_keys=False))
    return str(path)


def test_apply_adds_the_new_changes_the_named_and_deletes_nobody(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orchestrator = _Orchestrator(ADA, BO)
    changed_ada = {**ADA, "hours": "10-18", "identities": [{"kind": "slack", "value": "U024BE7LH"}]}
    new = {"name": "Cy", "identities": [{"kind": "gitlab", "value": "cy"}]}
    code = run(_args("apply", file=_file(tmp_path, [changed_ada, new])), transport=orchestrator.transport())
    assert code == OK, capsys.readouterr()
    writes = [(method, path, body) for method, path, body, _h in orchestrator.sent if method != "GET"]
    assert writes == [
        (
            "PATCH",
            f"/api/v1/people/{ADA['id']}",
            {
                **{
                    k: v
                    for k, v in changed_ada.items()
                    if k in ("name", "email", "user_id", "timezone", "hours", "weekdays", "disabled")
                },
                "identities": [{"kind": "slack", "value": "U024BE7LH"}],
            },
        ),
        ("POST", "/api/v1/people", {"name": "Cy", "identities": [{"kind": "gitlab", "value": "cy"}]}),
    ], "the named person is changed, the new one added, and Bo, left out of the file, is not deleted"
    patch_headers = next(h for method, _p, _b, h in orchestrator.sent if method == "PATCH")
    assert patch_headers.get("if-match") == f'"{ADA["updated_at"]}"', "a change is guarded by the exported version"
    out = capsys.readouterr().out
    assert "nobody is removed" in out and "+ Cy" in out and "changes hours" in out and "gives slack:u024be7lh" in out


def test_a_person_changed_since_the_export_is_a_conflict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    orchestrator = _Orchestrator(ADA, answer=409)
    code = run(_args("apply", file=_file(tmp_path, [{**ADA, "hours": "10-18"}])), transport=orchestrator.transport())
    assert code == CONFLICT, capsys.readouterr()


def test_check_names_every_problem_by_where_and_never_its_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = _file(
        tmp_path,
        [
            {"name": "Ok", "hours": "18-09"},
            {"email": "zz9-not-an-address", "identities": [{"kind": "github", "value": "zz9--x"}]},
            {**ADA},
            {**ADA},
        ],
    )
    assert run(_args("check", file=file)) == REFUSED
    err = capsys.readouterr().err
    for where in ("people.0.hours", "people.1.email", "people.1.name", "people.1.identities.0.value", "people.3.id"):
        assert where in err, f"{where} is not named: {err}"
    assert "zz9" not in err, f"a problem repeats a value: {err}"


def test_an_entry_naming_a_person_not_stored_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    orchestrator = _Orchestrator(BO)
    code = run(_args("diff", file=_file(tmp_path, [ADA])), transport=orchestrator.transport())
    assert code == REFUSED and "people.0.id" in capsys.readouterr().err
    assert all(method == "GET" for method, *_rest in orchestrator.sent), "diff writes nothing"


def test_export_writes_what_apply_reads_back_as_nothing_to_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exported = yaml.safe_load(export_document([ADA, BO]))
    assert [p["id"] for p in exported["people"]] == [ADA["id"], BO["id"]]
    orchestrator = _Orchestrator(ADA, BO)
    code = run(_args("apply", file=_file(tmp_path, exported["people"])), transport=orchestrator.transport())
    assert code == OK and "nothing to change" in capsys.readouterr().out
    assert [method for method, *_rest in orchestrator.sent] == ["GET"]


def test_a_change_without_the_version_it_was_exported_with_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#691 review: an entry naming a person but no updated_at would send no If-Match, or the stored version, and
    overwrite whatever changed since: identities added since, a user linked since."""
    unversioned = {key: value for key, value in ADA.items() if key != "updated_at"}
    file = _file(tmp_path, [{**unversioned, "hours": "10-18"}, {**BO, "updated_at": ""}])
    assert run(_args("check", file=file)) == REFUSED, "an entry naming a person but not its version passed the check"
    err = capsys.readouterr().err
    assert "people.0.updated_at" in err and "people.1.updated_at" in err, f"each entry is named: {err}"
    orchestrator = _Orchestrator(ADA, BO)
    assert run(_args("apply", file=file), transport=orchestrator.transport()) == REFUSED
    assert all(method == "GET" for method, *_rest in orchestrator.sent), "nothing is written"


def test_apply_stopped_partway_says_what_it_applied_and_what_it_did_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    orchestrator = _Orchestrator(ADA, BO, answers=[200, 409])
    file = _file(tmp_path, [{**ADA, "hours": "10-18"}, {**BO, "hours": "08-16"}, {"name": "Cy"}])
    assert run(_args("apply", file=file), transport=orchestrator.transport()) == CONFLICT
    err = capsys.readouterr().err
    assert "1 of 3 change(s) applied" in err, f"a refusal partway does not say what it applied: {err}"
    assert f"applied: ~ Ada ({ADA['id']})" in err, err
    assert f"not applied: ~ Bo ({BO['id']}), + Cy" in err, f"the refused change and every one after it: {err}"
    assert [method for method, *_rest in orchestrator.sent] == ["GET", "PATCH", "PATCH"], "it stops at the refusal"


def test_the_plan_itself_refuses_a_change_without_its_version() -> None:
    """The plan sends each entry's own version and never the stored one, which would match whatever is stored and
    so guard nothing (#691 review), even for a caller that skipped the file's checks."""
    from embry0.cli import people as cli

    unversioned = {key: value for key, value in ADA.items() if key != "updated_at"}
    try:
        cli._plan([(unversioned, {"hours": "10-18"}, None)], {ADA["id"]: ADA})
    except cli._Exit as refused:
        assert refused.code == REFUSED
    else:
        pytest.fail("the plan changes a person with no version of the entry's own, so nothing guards the change")
    [step] = cli._plan(
        [({**ADA, "updated_at": "2026-11-20T09:00:00+00:00"}, {"hours": "10-18"}, None)], {ADA["id"]: ADA}
    )
    assert step["if_match"] == "2026-11-20T09:00:00+00:00", "the version the entry was exported with, not the stored"

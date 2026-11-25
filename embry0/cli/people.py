"""embry0 people — check, diff, apply and export the deployment's people from a file (#179, spec 2026-11-23).

    embry0 people check people.yaml        # offline: the API's own checks, every problem by where it is
    embry0 people diff people.yaml         # what applying it would add and change (reads; writes nothing)
    embry0 people apply people.yaml        # the diff, a confirmation, then each add and change
    embry0 people export [-o FILE]         # every person as YAML, ready to edit and apply

The file is a list under ``people``, each entry a person as ``export``
writes it::

    people:
      - id: per-0123456789ab        # leave it out to add a person
        updated_at: "2026-11-25T09:00:00+00:00"
        name: Ada Lovelace
        email: ada@example.com
        user_id: usr-0123456789ab
        timezone: Europe/London
        hours: 09-17
        weekdays: mon-fri
        disabled: false
        identities:
          - {kind: github, value: ada-l}
          - {kind: slack, value: U024BE7LH}

``apply`` adds each entry without an ``id`` and changes each with one:
the fields it gives (``null`` clears an optional one), and with
``identities`` the person's identities made exactly those. Nothing is
deleted: a person left out of the file stays, and removing one is the API's
``DELETE /people/{id}``. An entry with an ``id`` must carry the
``updated_at`` it was exported with, which each change sends as
``If-Match``, so a person someone changed since is refused (409) rather than
overwritten, identities added since included; export again and redo the
change. ``apply`` stops at the first refusal and says which entries it
applied before it and which it did not. ``check``
runs offline with the very checks a write meets, and prints every problem
as ``where: message``, never the value. Writing people needs an admin key
without a repository allowlist.

Exit codes are :mod:`embry0.cli.exit_codes`': 0 done, 1 refused (a problem
in the file, or the answer at the prompt was no), 2 usage (a file that
cannot be read or written, or ``apply`` without a terminal and without
``--yes``), 3 conflict (a person changed since the file was exported, or a
value is someone else's), 4 unavailable (the orchestrator cannot be
reached, refused the key, or failed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx
import yaml

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED, UNAVAILABLE, USAGE
from embry0.cli.settings import problem_line, problem_lines

#: The largest file read: a directory of people is small.
MAX_FILE_BYTES: Final = 1024 * 1024
#: The fields an entry carries beside a person's own: what ``export`` writes and ``apply`` reads.
_SPECIAL: Final = ("id", "updated_at", "identities")


class _Exit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _refused(lines: list[str]) -> int:
    _err("refused:")
    for line in lines:
        _err(f"  {line}")
    return REFUSED


# ---- the file ----------------------------------------------------------------------


def _read(path: str) -> list[dict[str, Any]]:
    """The entries of a people file; refused (exit 1) with every problem of its shape, or 2 when it cannot be read."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        _err(f"{path}: cannot be read ({exc.strerror or exc})")
        raise _Exit(USAGE) from None
    if len(data) > MAX_FILE_BYTES:
        _err(f"{path}: the file is larger than {MAX_FILE_BYTES // 1024} KiB")
        raise _Exit(REFUSED)
    try:
        text = data.decode("utf-8")
        document = json.loads(text) if Path(path).suffix.lower() == ".json" else yaml.safe_load(text)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError):
        raise _Exit(_refused([f"{path}: not a UTF-8 YAML or JSON document"])) from None
    entries = document.get("people") if isinstance(document, dict) else None
    if not isinstance(entries, list) or set(document) - {"people"}:
        raise _Exit(_refused(["the file is a list of people under the one key 'people'"]))
    return entries


def _checked(entries: list[Any]) -> list[tuple[dict[str, Any], dict[str, Any], list[tuple[str, str]] | None]]:
    """Each entry as ``(entry, fields, identities)``, checked as a write is; refused with every problem at once."""
    from embry0.services.people import PeopleInvalid, check_write

    checked: list[tuple[dict[str, Any], dict[str, Any], list[tuple[str, str]] | None]] = []
    problems: list[str] = []
    ids: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(problem_line(["people", i], "a person is an object of fields"))
            continue
        person_id = entry.get("id")
        if person_id is not None and (not isinstance(person_id, str) or not person_id.strip()):
            problems.append(problem_line(["people", i, "id"], "an id is the text export wrote"))
        elif isinstance(person_id, str):
            if person_id in ids:
                problems.append(problem_line(["people", i, "id"], "the same person twice"))
            ids.add(person_id)
        exported = entry.get("updated_at")
        if exported is not None and not isinstance(exported, str):
            problems.append(problem_line(["people", i, "updated_at"], "the time export wrote, as text"))
        elif person_id is not None and not (exported or "").strip():
            problems.append(
                problem_line(
                    ["people", i, "updated_at"],
                    "a change names the version it was exported with: export again, or copy the person's updated_at",
                )
            )
        fields = {key: value for key, value in entry.items() if key not in _SPECIAL}
        try:
            values, identities = check_write(fields, entry.get("identities"), creating=person_id is None)
        except PeopleInvalid as exc:
            problems.extend(problem_line(["people", i, *loc], message) for loc, message in exc.problems)
            continue
        checked.append((entry, values, identities))
    if problems:
        raise _Exit(_refused(problems))
    return checked


# ---- the API ----------------------------------------------------------------------


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _detail(response: httpx.Response) -> str:
    body = _json(response)
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, str) else response.text[:500]


def _send(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request. Every answer but a success ends the command with its exit code."""
    try:
        response = client.request(method, path, **kwargs)
    except httpx.TransportError:
        _err(f"cannot reach the orchestrator at {client.base_url}: is the stack running? (embry0 start)")
        raise _Exit(UNAVAILABLE) from None
    status = response.status_code
    if status < 400:
        return response
    if status == 422:
        body = _json(response)
        raise _Exit(_refused(problem_lines(body.get("detail") if isinstance(body, dict) else None)))
    if status in (409, 412):
        _err(_detail(response))
        raise _Exit(CONFLICT)
    if status == 401:
        _err("the orchestrator rejected the key (401): set EMBRY0_API_KEY or pass --api-key")
    elif status == 403:
        _err(f"refused (403): {_detail(response)}; writing people needs an admin key without a repository allowlist")
    else:
        _err(f"the orchestrator answered {status}: {_detail(response)}")
    raise _Exit(UNAVAILABLE)


def _stored(client: httpx.Client) -> dict[str, dict[str, Any]]:
    body = _json(_send(client, "GET", "/api/v1/people", params={"include_disabled": "true"}))
    people = body.get("people") if isinstance(body, dict) else None
    return {str(p["id"]): p for p in people or [] if isinstance(p, dict) and p.get("id")}


def _identity_keys(identities: Any) -> set[tuple[str, str]]:
    return {(str(i.get("kind")), str(i.get("value")).lower()) for i in identities or [] if isinstance(i, dict)}


# ---- the plan -----------------------------------------------------------------------


def _plan(
    checked: list[tuple[dict[str, Any], dict[str, Any], list[tuple[str, str]] | None]],
    stored: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """What applying the file does: each person added, and each changed with what changes; an entry naming a person
    not stored is refused."""
    plan: list[dict[str, Any]] = []
    missing: list[str] = []
    for i, (entry, values, identities) in enumerate(checked):
        body: dict[str, Any] = dict(values)
        if identities is not None:
            body["identities"] = [{"kind": kind, "value": value} for kind, value in identities]
        person_id = entry.get("id")
        if person_id is None:
            plan.append({"add": values.get("name"), "body": body})
            continue
        current = stored.get(person_id)
        if current is None:
            missing.append(problem_line(["people", i, "id"], "no person has this id: leave it out to add one"))
            continue
        exported = entry.get("updated_at")
        if not isinstance(exported, str) or not exported.strip():
            missing.append(problem_line(["people", i, "updated_at"], "a change names the version it was exported with"))
            continue
        fields = sorted(key for key, value in values.items() if current.get(key) != value)
        added = removed = set[tuple[str, str]]()
        if identities is not None:
            want = {(kind, value.lower()) for kind, value in identities}
            held = _identity_keys(current.get("identities"))
            added, removed = want - held, held - want
        if fields or added or removed:
            plan.append(
                {
                    "change": person_id,
                    "name": current.get("name"),
                    "fields": fields,
                    "added": sorted(added),
                    "removed": sorted(removed),
                    "body": body,
                    "if_match": exported,
                }
            )
    if missing:
        raise _Exit(_refused(missing))
    return plan


def _show(plan: list[dict[str, Any]], stored: int) -> None:
    if not plan:
        print("nothing to change: every person in the file is stored as it is")
        return
    print("would change these people:")
    for step in plan:
        if "add" in step:
            print(f"  + {step['add']}: a new person")
            continue
        parts = [f"changes {', '.join(step['fields'])}"] if step["fields"] else []
        parts += [f"gives {kind}:{value}" for kind, value in step["added"]]
        parts += [f"takes {kind}:{value}" for kind, value in step["removed"]]
        print(f"  ~ {step['name']} ({step['change']}): {'; '.join(parts)}")
    print(f"nobody is removed: the {stored} people stored stay, whether the file names them or not")


def _confirmed(args: argparse.Namespace, question: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        _err("refusing to write without a terminal to confirm on; pass --yes")
        raise _Exit(USAGE)
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ---- the verbs ----------------------------------------------------------------------


def _check(args: argparse.Namespace) -> int:
    checked = _checked(_read(args.file))
    print(f"the people file is valid: {len(checked)} people")
    return OK


def _diff(args: argparse.Namespace, client: httpx.Client) -> int:
    checked = _checked(_read(args.file))
    stored = _stored(client)
    _show(_plan(checked, stored), len(stored))
    return OK


def _apply(args: argparse.Namespace, client: httpx.Client) -> int:
    checked = _checked(_read(args.file))
    stored = _stored(client)
    plan = _plan(checked, stored)
    _show(plan, len(stored))
    if not plan:
        return OK
    if not _confirmed(args, f"apply these {len(plan)} change(s)?"):
        _err("nothing written")
        return REFUSED
    done: list[str] = []
    for n, step in enumerate(plan):
        try:
            if "add" in step:
                made = _json(_send(client, "POST", "/api/v1/people", json=step["body"]))
                print(f"added {made.get('name')} ({made.get('id')})")
            else:
                path = "/api/v1/people/" + quote(step["change"], safe="")
                _send(client, "PATCH", path, json=step["body"], headers={"If-Match": f'"{step["if_match"]}"'})
                print(f"changed {step['name']} ({step['change']})")
        except _Exit:
            _stopped(done, plan[n:])
            raise
        done.append(_named(step))
    return OK


def _named(step: dict[str, Any]) -> str:
    return f"+ {step['add']}" if "add" in step else f"~ {step['name']} ({step['change']})"


def _stopped(done: list[str], left: list[dict[str, Any]]) -> None:
    """What a refusal partway through ``apply`` left: the changes made before it, and the ones not made."""
    _err(f"stopped at {_named(left[0])}: {len(done)} of {len(done) + len(left)} change(s) applied")
    _err(f"applied: {', '.join(done) if done else 'none'}")
    _err(f"not applied: {', '.join(_named(step) for step in left)}")


def export_document(people: list[dict[str, Any]]) -> str:
    """Every person as the file ``apply`` reads, in the order the API lists them."""
    entries = []
    for person in people:
        entry = {key: person.get(key) for key in ("id", "updated_at", "name", "email", "user_id")}
        entry.update({key: person.get(key) for key in ("timezone", "hours", "weekdays", "disabled")})
        entry["identities"] = [{"kind": i["kind"], "value": i["value"]} for i in person.get("identities") or []]
        entries.append(entry)
    return yaml.safe_dump({"people": entries}, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _export(args: argparse.Namespace, client: httpx.Client) -> int:
    text = export_document(list(_stored(client).values()))
    if args.output:
        try:
            Path(args.output).write_text(text, encoding="utf-8")
        except OSError as exc:
            _err(f"{args.output}: cannot be written ({exc.strerror or exc})")
            return USAGE
        _err(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return OK


def offline(args: argparse.Namespace) -> bool:
    """Whether this verb runs without the orchestrator."""
    return bool(args.action == "check")


def run(
    args: argparse.Namespace,
    *,
    base_url: str = "http://localhost:8200",
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Run one verb; answers its exit code. ``transport`` stands in for the network in tests."""
    try:
        if args.action == "check":
            return _check(args)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=httpx.Timeout(60.0, connect=10.0), transport=transport
        ) as client:
            verbs = {"diff": _diff, "apply": _apply, "export": _export}
            return verbs[args.action](args, client)
    except _Exit as done:
        return done.code


_FILE_HELP: Final = "the people file: YAML, or JSON when the file is named *.json"


def add_parser(subparsers: Any) -> None:
    """The ``people`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "people",
        help="Check, diff, apply or export the deployment's people from a file",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    verbs = parser.add_subparsers(dest="action", required=True)
    check = verbs.add_parser("check", help="Validate a people file offline, every problem by where; exit 1 on one")
    check.add_argument("file", help=_FILE_HELP)
    diff = verbs.add_parser("diff", help="Show what applying a people file would add and change (writes nothing)")
    diff.add_argument("file", help=_FILE_HELP)
    apply = verbs.add_parser("apply", help="Show the diff, then add and change the people it names (deletes nobody)")
    apply.add_argument("file", help=_FILE_HELP)
    apply.add_argument("--yes", action="store_true", help="apply without asking (needed without a terminal)")
    export = verbs.add_parser("export", help="Write every person as YAML (stdout, or -o FILE)")
    export.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    for verb in (diff, apply, export):
        verb.add_argument(
            "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
        )
        verb.add_argument(
            "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
        )

"""embry0 settings — check, diff, apply and export the settings document from a file (#162).

    embry0 settings check settings.yaml      # offline: the API's own checks, every problem by key
    embry0 settings diff settings.yaml       # what applying it would change (a dry run; stores nothing)
    embry0 settings apply settings.yaml      # the diff, a confirmation, then store it (source "file")
    embry0 settings export [-o FILE]         # the stored document as written, comments included
    embry0 settings export --default         # every section, every key at its default, as comments
    embry0 settings import-env [-o FILE]     # a document holding the policy variables this environment sets
    embry0 settings revisions [ID]           # the document's changes, newest first; or one, with its diff
    embry0 settings undo ID                  # put back what the newest change replaced

    embry0 settings apply --org acme acme.yaml            # an organization's layer (spec 2026-10-29)
    embry0 settings diff --repo acme/widgets widgets.yaml # a repository's layer

An operator who keeps the deployment's settings in their own repository
reviews a change as a file and stores it with ``apply``; the verbs read like
``embry0 progression-pack``.

``check`` runs offline, against the sections of the embry0 it runs from,
with the very checks a write meets, and prints the same messages the API's
422 carries, one per problem, as ``key: message``, never the value. Offline,
because a file is checked where it is written (a laptop, a CI job), with no
orchestrator and no key: ``diff`` is the same check made by the
orchestrator, against its own sections and the stored document. The two
differ only for what a newer release wrote, a section or a key of a known
section: the orchestrator keeps it while it is sent back as stored, and
``check`` cannot see what is stored. A file named ``*.json`` is read and
sent as JSON, any other as YAML.

``diff``, ``apply``, ``export``, ``revisions`` and ``undo`` go through the API
like ``embry0 onboard`` (``EMBRY0_API_URL``, the key from ``EMBRY0_API_KEY``,
``API_KEY`` or ``./.env``). ``apply`` reads the document's generation first,
diffs against it and writes with ``If-Match`` naming it, so a document
someone changed in between is refused (409) rather than overwritten; a file
that is the stored text already sends nothing more, so it records no
revision. A key that is set now and that the file leaves out goes back to
its default: ``apply`` names each one, and with ``--yes`` it unsets them
only with ``--allow-unset`` too, since a key the startup import wrote into
the stored document (and not into the file) would be lost without a word.
``export --default`` and ``import-env`` run offline too.

``--org OWNER`` or ``--repo PATH`` (``group/name``, or a GitLab subgroup's
``group/subgroup/name``) on ``check``, ``diff``, ``apply``,
``export`` and ``revisions`` names an organization's or a repository's layer
instead of the deployment's document: a document of the same shape holding
only what that organization or repository sets. ``check`` then also refuses
a key the layer may not set; whether its values combine with what the layer
inherits needs the stored documents, so ``diff`` says. ``undo`` takes the
change's number, which names its layer. ``import-env`` and ``export
--default`` are the deployment's only.

Exit codes, for scripts, the same as ``embry0 progression-pack``'s
(:mod:`embry0.cli.exit_codes`):

    0  done: valid, applied, nothing to change, written, undone
    1  refused: the document has problems (the API's 422, or ``check``),
       a variable the environment sets is refused, or the answer was no
    2  usage: a bad argument, a file that cannot be read or written,
       ``apply`` or ``undo`` without a terminal to confirm on and without
       ``--yes``, or ``apply --yes`` that would unset a key without
       ``--allow-unset``
    3  conflict: the document changed since it was read (409), or the
       change to undo is not the newest one, or no longer what is stored
    4  unavailable: the orchestrator cannot be reached, refused the key
       (401, 403), has no such revision, or failed
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import httpx

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED, SUMMARY, UNAVAILABLE, USAGE

_SETTINGS: Final = "/api/v1/config/settings"
_YAML: Final = {"Content-Type": "application/yaml"}
#: The longest ``--reason`` the API stores with a revision.
MAX_REASON: Final = 500


class _Exit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---- what is printed ----------------------------------------------------------


def problem_line(loc: Iterable[Any], msg: str) -> str:
    """One problem as ``check``, ``diff`` and ``apply`` print it: ``key: message``, as the dashboard shows it.

    ``loc`` is the path from the document's root; empty (a YAML syntax
    error, an empty file of the wrong kind) prints the message alone.
    """
    path = ".".join(str(part) for part in loc)
    text = msg.strip().removeprefix("Value error, ")
    return f"{path}: {text}" if path else text


def problem_lines(detail: Any) -> list[str]:
    """A 422's detail as lines; ``input`` is never read, so no value is printed."""
    if isinstance(detail, str):
        return [detail]
    lines: list[str] = []
    for item in detail if isinstance(detail, list) else []:
        if isinstance(item, dict) and isinstance(item.get("msg"), str):
            loc = item.get("loc")
            lines.append(problem_line(loc if isinstance(loc, list) else [], item["msg"]))
    return lines or ["the document was refused"]


def _refused(lines: Iterable[str]) -> int:
    _err("the settings document has problems:")
    for line in lines:
        _err(f"  {line}")
    return REFUSED


def _value(set_: bool, value: Any, effective: Any) -> str:
    if set_:
        return json.dumps(value)
    if effective is None:
        return "not set"
    return f"{json.dumps(effective)} (default)"


def _change_lines(changes: Iterable[dict[str, Any]], *, reverse: bool = False) -> list[str]:
    """Each changed key: ``asks.hours: "09-18" (default) -> "08-17"``; ``reverse`` reads it the other way."""
    lines: list[str] = []
    for change in changes:
        before = _value(bool(change.get("set_before")), change.get("before"), change.get("effective_before"))
        after = _value(bool(change.get("set_after")), change.get("after"), change.get("effective_after"))
        if reverse:
            before, after = after, before
        lines.append(f"  {change.get('key')}: {before} -> {after}")
    return lines


def _unset(changes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The changes that take a key that is set now out of the document: it goes back to its default."""
    return [change for change in changes if change.get("set_before") and not change.get("set_after")]


def _unknown(body: dict[str, Any]) -> str | None:
    parts = [*(f"section {name}" for name in body.get("unknown_sections") or [])]
    parts += [f"key {name}" for name in body.get("unknown_keys") or []]
    return ", ".join(parts) or None


def _show(preview: dict[str, Any]) -> None:
    """A dry run: whether it would change, each key it changes, and the text diff."""
    changes = preview.get("changes") or []
    print(f"would change: {'yes' if preview.get('would_change') else 'no'}")
    if changes:
        print("settings:")
        for line in _change_lines(changes):
            print(line)
    elif preview.get("would_change"):
        print("settings: none; only comments or layout change")
    unset = _unset(changes)
    if unset:
        print("unsets (set now, left out of the file, so back to the default):")
        for line in _change_lines(unset):
            print(line)
    unknown = _unknown(preview)
    if unknown:
        print(f"kept as stored, not read by this release: {unknown}")
    diff = preview.get("text_diff") or ""
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")


# ---- files ----------------------------------------------------------------------


def _read(path: str) -> str:
    """The file's text. A file that cannot be read is a usage error; one the API would refuse is refused."""
    from embry0.settings.document import MAX_TEXT_BYTES

    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as exc:
        _err(f"{path}: cannot be read ({exc.strerror or exc})")
        raise _Exit(USAGE) from None
    if len(data) > MAX_TEXT_BYTES:
        _err(f"{path}: the settings document is larger than {MAX_TEXT_BYTES // 1024} KiB")
        raise _Exit(REFUSED)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        _err(f"{path}: the document must be UTF-8 text")
        raise _Exit(REFUSED) from None


def _content_type(path: str) -> str:
    """How the file is read and sent: a ``*.json`` file as JSON, any other as YAML."""
    return "application/json" if Path(path).suffix.lower() == ".json" else "application/yaml"


def _write(text: str, output: str | None) -> None:
    if output:
        try:
            Path(output).write_text(text, encoding="utf-8")
        except OSError as exc:
            _err(f"{output}: cannot be written ({exc.strerror or exc})")
            raise _Exit(USAGE) from None
        _err(f"wrote {output}")
    else:
        sys.stdout.write(text)


# ---- the API ----------------------------------------------------------------------


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _detail(response: httpx.Response) -> str:
    detail = _json(response).get("detail")
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
        raise _Exit(_refused(problem_lines(_json(response).get("detail"))))
    if status == 413:
        _err(f"refused (413): {_detail(response)}")
        raise _Exit(REFUSED)
    if status == 409:
        _err(_detail(response))
        raise _Exit(CONFLICT)
    if status == 401:
        _err("the orchestrator rejected the key (401): set EMBRY0_API_KEY or pass --api-key")
    elif status == 403:
        _err(
            f"refused (403): {_detail(response)}; changing the settings document needs an admin key without a "
            "repository allowlist, or for a repository's layer one whose allowlist covers it"
        )
    else:
        _err(f"the orchestrator answered {status}: {_detail(response)}")
    raise _Exit(UNAVAILABLE)


def _layer(args: argparse.Namespace) -> dict[str, str]:
    """The query that names the layer ``--org`` or ``--repo`` asks for; neither names the deployment's document."""
    org, repo = getattr(args, "org", None), getattr(args, "repo", None)
    if org:
        return {"owner": org}
    if repo:
        return {"repo": repo}
    return {}


def _level(args: argparse.Namespace) -> str:
    """The layer ``check`` checks a file as: ``deployment``, ``organization`` or ``repository``."""
    if getattr(args, "org", None):
        return "organization"
    if getattr(args, "repo", None):
        return "repository"
    return "deployment"


def _generation(client: httpx.Client, layer: dict[str, str]) -> int:
    """The stored document's generation, from its ``ETag``; 0 for a layer nobody wrote yet."""
    response = _send(client, "GET", _SETTINGS, params=layer)
    etag = response.headers.get("etag", "").strip('"')
    if etag.isdigit():
        return int(etag)
    return int(_json(response).get("generation") or 0)


def _dry_run(
    client: httpx.Client, text: str, content_type: str, layer: dict[str, str], generation: int | None = None
) -> dict[str, Any]:
    headers = {"Content-Type": content_type}
    if generation is not None:
        headers["If-Match"] = f'"{generation}"'
    response = _send(
        client,
        "PUT",
        _SETTINGS,
        params={"dry_run": 1, "source": "file", **layer},
        content=text.encode("utf-8"),
        headers=headers,
    )
    return _json(response)


def _confirmed(args: argparse.Namespace, question: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        _err("refusing to write without a terminal to confirm on; pass --yes")
        raise _Exit(USAGE)
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ---- the verbs ----------------------------------------------------------------------


def _check(args: argparse.Namespace) -> int:
    from embry0.settings.document import DocumentInvalid, DocumentTooLarge, check, parse_body
    from embry0.settings.registry import MANAGED_BY_KEY
    from embry0.settings.sections import CATALOG

    text = _read(args.file)
    try:
        _, values = parse_body(text.encode("utf-8"), _content_type(args.file))
        # A layer's values alone: what they combine with is stored, so `diff` checks that.
        checked = check(CATALOG, values, stored={}, layer=_level(args), inherited={})
    except DocumentTooLarge as exc:
        _err(str(exc))
        return REFUSED
    except DocumentInvalid as exc:
        return _refused(problem_line(p.loc, p.msg) for p in exc.problems)
    written = [
        f"{name}.{key}" for name, section in checked.data.items() if isinstance(section, dict) for key in section
    ]
    if MANAGED_BY_KEY in checked.data:
        written.insert(0, MANAGED_BY_KEY)
    what = {"organization": "organization layer", "repository": "repository layer"}.get(_level(args), "document")
    print(f"the settings {what} is valid; it sets {', '.join(written) if written else 'nothing'}")
    return OK


def _diff(args: argparse.Namespace, client: httpx.Client) -> int:
    _show(_dry_run(client, _read(args.file), _content_type(args.file), _layer(args)))
    return OK


def _apply(args: argparse.Namespace, client: httpx.Client) -> int:
    if len(args.reason) > MAX_REASON:
        _err(f"--reason is longer than {MAX_REASON} characters")
        return USAGE
    text = _read(args.file)
    content_type = _content_type(args.file)
    layer = _layer(args)
    generation = _generation(client, layer)
    preview = _dry_run(client, text, content_type, layer, generation)
    _show(preview)
    if not preview.get("would_change"):
        print("nothing to apply: the stored document is this text already, so no revision is recorded")
        return OK
    unset = _unset(preview.get("changes") or [])
    if unset and args.yes and not args.allow_unset:
        # A key the startup import wrote into the stored document is in no file: unsetting it unasked loses it.
        _err(
            f"refusing to unset {', '.join(str(change.get('key')) for change in unset)} without --allow-unset: "
            "each is set now and the file leaves it out. Copy the keys you keep into the file, or pass "
            "--allow-unset to put them back to their defaults"
        )
        return USAGE
    question = f"Store this document over generation {generation}?"
    if unset:
        question = f"Store this document over generation {generation}, unsetting {len(unset)} key(s)?"
    if not _confirmed(args, question):
        _err("not applied")
        return REFUSED
    params: dict[str, Any] = {"source": "file", **layer}
    if args.reason:
        params["reason"] = args.reason
    # The generation the diff above was taken against: a document changed since is refused, not overwritten.
    response = _send(
        client,
        "PUT",
        _SETTINGS,
        params=params,
        content=text.encode("utf-8"),
        headers={"Content-Type": content_type, "If-Match": f'"{generation}"'},
    )
    result = _json(response)
    if result.get("revision_id") is None:
        print("nothing changed, so no revision is recorded")
    else:
        print(f"applied: generation {result.get('generation')}, revision {result.get('revision_id')}")
    return OK


def _export_default(args: argparse.Namespace) -> int:
    from embry0.settings.example import render
    from embry0.settings.sections import CATALOG

    _write(render(CATALOG), args.output)
    return OK


def _export(args: argparse.Namespace, client: httpx.Client) -> int:
    layer = _layer(args)
    response = _send(client, "GET", _SETTINGS, params={"format": "yaml", **layer})
    if not response.text.strip():
        _err(
            "the stored layer is empty: every key takes what it inherits"
            if layer
            else "the stored document is empty: every key takes its default"
        )
    _write(response.text, args.output)
    return OK


def _import_env(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    if _layer(args):
        _err("import-env writes the deployment's document: the environment is the deployment's, not a layer's")
        return USAGE

    from embry0.config import Embry0Config
    from embry0.config_redaction import mask_url_credentials, validation_error_lines
    from embry0.settings.bootstrap import env_values
    from embry0.settings.document import with_section
    from embry0.settings.registry import SCHEMA_VERSION, VERSION_KEY
    from embry0.settings.sections import CATALOG

    try:
        config = Embry0Config()
    except ValidationError as exc:
        # Each variable by name and why, never its value: it may be any secret the environment holds.
        _err("the environment does not load:")
        for line in validation_error_lines(Embry0Config, exc):
            _err(f"  {line}")
        return REFUSED
    except Exception as exc:  # noqa: BLE001 - as `embry0 config` reports it
        _err(f"the environment does not load: {mask_url_credentials(str(exc))}")
        return REFUSED
    found = env_values(CATALOG, config)
    day = datetime.now(UTC).date().isoformat()
    text = (
        "# Written by `embry0 settings import-env` from the policy variables of the environment it ran in.\n"
        "# Every key it does not write takes its default.\n"
        f"{VERSION_KEY}: {SCHEMA_VERSION}\n"
    )
    data: dict[str, Any] = {}
    for name, values in found.values.items():
        comment = f"Imported from the environment on {day}: {', '.join(found.variables(CATALOG, name))}."
        text = with_section(text, data, name, values, comment)
        data[name] = values
    _write(text, args.output)
    for item in found.refused:
        _err(f"{item.variable} ({item.section}.{item.key}) is refused, so it is left out: {item.reason}")
    for item in found.held:
        _err(
            f"{item.variable} ({item.section}.{item.key}) is refused together with the section's other values, "
            f"so it is left out: {item.reason}"
        )
    if not found.values and not (found.refused or found.held):
        _err("no policy variable is set to anything but its default: the document sets nothing")
    return REFUSED if found.refused or found.held else OK


def _revisions(args: argparse.Namespace, client: httpx.Client) -> int:
    if args.id is not None:
        return _revision(args.id, client)
    params = {"limit": args.limit, **_layer(args)}
    rows = _json(_send(client, "GET", f"{_SETTINGS}/revisions", params=params)).get("revisions") or []
    if not rows:
        print("no change is recorded yet")
        return OK
    for row in rows:
        _heading(row)
        if row.get("undoable"):
            print(f"    the newest change: `embry0 settings undo {row.get('id')}` puts back what it replaced")
    return OK


def _heading(row: dict[str, Any]) -> None:
    """A revision's first lines: its number, when, how, who, what it changed and why."""
    print(f"#{row.get('id')}  {row.get('created_at') or '-'}  {row.get('source')}  {row.get('actor') or '-'}")
    print(f"    {row.get('what')}")
    if row.get("reason"):
        print(f"    reason: {row['reason']}")


def _revision(revision_id: int, client: httpx.Client) -> int:
    from embry0.text_documents import text_diff

    row = _json(_send(client, "GET", f"{_SETTINGS}/revisions/{revision_id}"))
    _heading(row)
    for line in _change_lines(row.get("changes") or []):
        print(line)
    diff = text_diff(
        row.get("before_text") or "",
        row.get("after_text") or "",
        before_name=f"before #{revision_id}",
        after_name=f"after #{revision_id}",
    )
    if diff:
        print(diff, end="")
    if not row.get("undoable") and row.get("why_not"):
        print(f"undo: {row['why_not']}")
    return OK


def _undo(args: argparse.Namespace, client: httpx.Client) -> int:
    row = _json(_send(client, "GET", f"{_SETTINGS}/revisions/{args.id}"))
    if not row.get("undoable"):
        _err(f"#{args.id} cannot be undone: {row.get('why_not') or 'only the newest change can be'}")
        return CONFLICT
    from embry0.text_documents import text_diff

    changes = row.get("changes") or []
    print(f"undoing #{args.id} ({row.get('what')}) puts back " + ("these settings:" if changes else "its text:"))
    for line in _change_lines(changes, reverse=True):
        print(line)
    # The text it puts back, read the way the undo goes; for a change of comments only, the whole of it.
    diff = text_diff(
        row.get("after_text") or "",
        row.get("before_text") or "",
        before_name=f"now (after #{args.id})",
        after_name="after the undo",
    )
    if diff:
        print(diff, end="")
    if not _confirmed(args, f"Undo change #{args.id}?"):
        _err("not undone")
        return REFUSED
    result = _json(_send(client, "POST", f"{_SETTINGS}/revisions/{args.id}/undo"))
    print(f"undone: generation {result.get('generation')}, recorded as revision {result.get('revision_id')}")
    return OK


#: Verbs that need no orchestrator.
OFFLINE: Final = frozenset({"check", "import-env"})


def offline(args: argparse.Namespace) -> bool:
    """Whether this verb runs without the orchestrator."""
    return args.action in OFFLINE or (args.action == "export" and bool(args.default))


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
        if args.action == "import-env":
            return _import_env(args)
        if args.action == "export" and args.default:
            if _layer(args):
                _err("export --default writes the deployment's defaults; a layer holds only what it sets")
                return USAGE
            return _export_default(args)
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        with httpx.Client(
            base_url=base_url, headers=headers, timeout=httpx.Timeout(60.0, connect=10.0), transport=transport
        ) as client:
            verbs = {"diff": _diff, "apply": _apply, "export": _export, "revisions": _revisions, "undo": _undo}
            return verbs[args.action](args, client)
    except _Exit as done:
        return done.code


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number from 1 to 100") from None
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError("must be a whole number from 1 to 100")
    return number


_FILE_HELP: Final = "the settings document: YAML, or JSON when the file is named *.json"


def add_parser(subparsers: Any) -> None:
    """The ``settings`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "settings",
        help="Check, diff, apply or export the settings document (operator policy) from a file",
        description=SUMMARY,
    )
    verbs = parser.add_subparsers(dest="action", required=True)
    check = verbs.add_parser(
        "check", help="Validate a settings file offline, every problem by key; exit 1 on a problem"
    )
    check.add_argument("file", help=_FILE_HELP)
    diff = verbs.add_parser("diff", help="Show what applying a settings file would change (a dry run)")
    diff.add_argument("file", help=_FILE_HELP)
    apply = verbs.add_parser("apply", help="Show the diff, then store a settings file (source: file)")
    apply.add_argument("file", help=_FILE_HELP)
    apply.add_argument("--yes", action="store_true", help="apply without asking (needed without a terminal)")
    apply.add_argument(
        "--allow-unset",
        action="store_true",
        help="with --yes, also unset keys that are set now and that the file leaves out",
    )
    apply.add_argument("--reason", default="", help=f"why, for the revision history (at most {MAX_REASON} characters)")
    export = verbs.add_parser("export", help="Write the stored settings document as YAML (stdout, or -o FILE)")
    export.add_argument(
        "--default", action="store_true", help="every section with every key at its default instead (offline)"
    )
    export.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    import_env = verbs.add_parser(
        "import-env", help="Write a document from the policy variables this environment sets (offline)"
    )
    import_env.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    revisions = verbs.add_parser("revisions", help="List the document's changes, newest first, or show one")
    revisions.add_argument("id", nargs="?", type=int, default=None, help="show this change with its diff")
    revisions.add_argument("--limit", type=_limit, default=20, help="how many to list, 1 to 100 (default 20)")
    undo = verbs.add_parser("undo", help="Put back what the newest change replaced (recorded as a restore)")
    undo.add_argument("id", type=int, help="the newest change's number, from `embry0 settings revisions`")
    undo.add_argument("--yes", action="store_true", help="undo without asking (needed without a terminal)")
    for verb in (check, diff, apply, export, import_env, revisions):
        layer = verb.add_mutually_exclusive_group()
        layer.add_argument("--org", default=None, metavar="OWNER", help="an organization's layer, by its forge owner")
        layer.add_argument(
            "--repo", default=None, metavar="PATH", help="a repository's layer: group/name, or group/subgroup/name"
        )
    for verb in (diff, apply, export, revisions, undo):
        verb.add_argument(
            "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
        )
        verb.add_argument(
            "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
        )

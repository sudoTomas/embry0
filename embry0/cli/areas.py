"""embry0 areas — check, diff, apply and export a repository's areas from a file (spec 2026-11-10).

    embry0 areas check areas.yaml                  # offline: the API's own checks, every problem by key
    embry0 areas diff acme/widgets areas.yaml      # what applying it would change (a dry run; stores nothing)
    embry0 areas apply acme/widgets areas.yaml     # the diff, a confirmation, then store it (source "file")
    embry0 areas export acme/widgets [-o FILE]     # the stored document as written, comments included

A repository's areas are a document embry0 keeps, never a file in the
repository. An operator who keeps them in a repository of their own reviews a
change as a file and stores it with ``apply``; the verbs read like
``embry0 settings``. ``check`` runs offline, with the very checks a write
meets, and prints the API's messages, one per problem, as ``key: message``,
never the value. ``diff``, ``apply`` and ``export`` go through the API like
``embry0 settings`` (``EMBRY0_API_URL``, the key from ``EMBRY0_API_KEY``,
``API_KEY`` or ``./.env``). ``apply`` reads the stored generation first,
diffs against it and writes with ``If-Match`` naming it, so areas someone
changed in between are refused (409) rather than overwritten; a file that is
the stored text already records nothing. Undo a change from the
repository's History, or with the MCP tool ``areas_undo``. A file named
``*.json`` is read and sent as JSON, any other as YAML.

Exit codes are :mod:`embry0.cli.exit_codes`': 0 done, 1 refused (a problem
in the file, or the answer at the prompt was no), 2 usage (a file that
cannot be read or written, or ``apply`` without a terminal and without
``--yes``), 3 conflict (the areas changed since they were read), 4
unavailable (the orchestrator cannot be reached, refused the key, has no
such repository, or failed).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import httpx

from embry0.cli.exit_codes import CONFLICT, OK, REFUSED, SUMMARY, UNAVAILABLE, USAGE
from embry0.cli.settings import problem_line, problem_lines

_YAML: Final = "application/yaml"
#: The longest ``--reason`` the API stores with a revision.
MAX_REASON: Final = 500


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


def _path(repo: str) -> str:
    """The areas route of ``repo``, each segment quoted (``repo`` is checked first, by :func:`_checked_repo`)."""
    return "/api/v1/repos/" + "/".join(quote(segment, safe="") for segment in repo.split("/")) + "/-/areas"


def _checked_repo(repo: str) -> str:
    """``repo`` when it is a repository's path, the rule the API reads one by (``group/name``, or
    ``group/subgroup/name``); otherwise a usage error before anything is sent, so ``acme/web?format=json``
    never reaches another route."""
    from embry0.api.schemas.repo_path import validate_repo_path

    try:
        return validate_repo_path(repo)
    except ValueError as exc:
        _err(f"{exc}: {repo!r} is not one")
        raise _Exit(USAGE) from None


# ---- files ----------------------------------------------------------------------


def _read(path: str) -> str:
    from embry0.services.areas_document import MAX_TEXT_BYTES

    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        _err(f"{path}: cannot be read ({exc.strerror or exc})")
        raise _Exit(USAGE) from None
    if len(data) > MAX_TEXT_BYTES:
        _err(f"{path}: the areas document is larger than {MAX_TEXT_BYTES // 1024} KiB")
        raise _Exit(REFUSED)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        _err(f"{path}: the document must be UTF-8 text")
        raise _Exit(REFUSED) from None


def _content_type(path: str) -> str:
    return "application/json" if Path(path).suffix.lower() == ".json" else _YAML


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


# ---- what is printed ----------------------------------------------------------


def change_lines(changes: list[dict[str, Any]]) -> list[str]:
    """Each area a change adds, removes or changes, as ``diff`` and ``apply`` print it, owners first."""
    lines: list[str] = []
    for change in changes:
        what = change.get("change")
        line = f"  {change.get('area')}: " + (
            f"changes {', '.join(change.get('keys') or [])}" if what == "changed" else str(what)
        )
        if change.get("owners_added"):
            line += f"; makes {', '.join(change['owners_added'])} owner(s)"
        if change.get("owners_removed"):
            line += f"; no longer asks {', '.join(change['owners_removed'])}"
        lines.append(line)
    return lines


def _show(preview: dict[str, Any]) -> None:
    if not preview.get("would_change"):
        print("nothing to change: the file is the text already stored")
        return
    changes = preview.get("changes") or []
    if changes:
        print("would change these areas:")
        for line in change_lines(changes):
            print(line)
    else:
        print("only comments or layout would change; no area does")
    diff = preview.get("text_diff") or ""
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")


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
            f"refused (403): {_detail(response)}; changing a repository's areas needs an operate key whose "
            "repository allowlist covers it"
        )
    else:
        _err(f"the orchestrator answered {status}: {_detail(response)}")
    raise _Exit(UNAVAILABLE)


def _generation(client: httpx.Client, repo: str) -> int:
    response = _send(client, "GET", _path(repo))
    etag = response.headers.get("etag", "").strip('"')
    return int(etag) if etag.isdigit() else int(_json(response).get("generation") or 0)


def _put(
    client: httpx.Client, repo: str, text: str, content_type: str, generation: int, *, dry_run: bool, reason: str = ""
) -> dict[str, Any]:
    params: dict[str, Any] = {"source": "file"}
    if dry_run:
        params["dry_run"] = 1
    if reason:
        params["reason"] = reason
    response = _send(
        client,
        "PUT",
        _path(repo),
        params=params,
        content=text.encode("utf-8"),
        headers={"Content-Type": content_type, "If-Match": f'"{generation}"'},
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
    from embry0.services.areas_document import DocumentInvalid, DocumentTooLarge, check, parse_body

    text = _read(args.file)
    try:
        _, values = parse_body(text.encode("utf-8"), _content_type(args.file))
        data = check(values)
    except DocumentTooLarge as exc:
        _err(str(exc))
        return REFUSED
    except DocumentInvalid as exc:
        return _refused([problem_line(p.loc, p.msg) for p in exc.problems])
    names = [area["name"] for area in data["areas"]]
    print(f"the areas document is valid; it names {', '.join(names) if names else 'no areas'}")
    return OK


def _diff(args: argparse.Namespace, client: httpx.Client) -> int:
    text = _read(args.file)
    generation = _generation(client, args.repo)
    _show(_put(client, args.repo, text, _content_type(args.file), generation, dry_run=True))
    return OK


def _apply(args: argparse.Namespace, client: httpx.Client) -> int:
    reason = args.reason.strip()
    if len(reason) > MAX_REASON:
        _err(f"--reason is at most {MAX_REASON} characters")
        return USAGE
    text, content_type = _read(args.file), _content_type(args.file)
    generation = _generation(client, args.repo)
    preview = _put(client, args.repo, text, content_type, generation, dry_run=True)
    _show(preview)
    if not preview.get("would_change"):
        return OK
    if not _confirmed(args, f"store these areas for {args.repo}?"):
        _err("nothing stored")
        return REFUSED
    saved = _put(client, args.repo, text, content_type, generation, dry_run=False, reason=reason)
    print(
        f"stored: generation {saved.get('generation')}, change {saved.get('revision_id')} in the repository's history"
    )
    return OK


def _export(args: argparse.Namespace, client: httpx.Client) -> int:
    response = _send(client, "GET", _path(args.repo), params={"format": "yaml"})
    _write(response.text, args.output)
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
        _checked_repo(args.repo)
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


_FILE_HELP: Final = "the areas document: YAML, or JSON when the file is named *.json"
_REPO_HELP: Final = "the repository: group/name, or group/subgroup/name"


def add_parser(subparsers: Any) -> None:
    """The ``areas`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "areas",
        help="Check, diff, apply or export a repository's areas (kept in embry0) from a file",
        description=SUMMARY,
    )
    verbs = parser.add_subparsers(dest="action", required=True)
    check = verbs.add_parser("check", help="Validate an areas file offline, every problem by key; exit 1 on a problem")
    check.add_argument("file", help=_FILE_HELP)
    diff = verbs.add_parser("diff", help="Show what applying an areas file would change (a dry run)")
    diff.add_argument("repo", help=_REPO_HELP)
    diff.add_argument("file", help=_FILE_HELP)
    apply = verbs.add_parser("apply", help="Show the diff, then store an areas file (source: file)")
    apply.add_argument("repo", help=_REPO_HELP)
    apply.add_argument("file", help=_FILE_HELP)
    apply.add_argument("--yes", action="store_true", help="apply without asking (needed without a terminal)")
    apply.add_argument("--reason", default="", help=f"why, for the history (at most {MAX_REASON} characters)")
    export = verbs.add_parser("export", help="Write a repository's stored areas as YAML (stdout, or -o FILE)")
    export.add_argument("repo", help=_REPO_HELP)
    export.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    for verb in (diff, apply, export):
        verb.add_argument(
            "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
        )
        verb.add_argument(
            "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
        )

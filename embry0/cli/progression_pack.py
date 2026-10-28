"""embry0 progression-pack — check, diff, apply and export the progression content pack (#176).

    embry0 progression-pack check my-pack.yaml     # offline: the API's own validator, no orchestrator needed
    embry0 progression-pack diff my-pack.yaml      # what applying it would change (a dry run, stores nothing)
    embry0 progression-pack apply my-pack.yaml     # the diff, a confirmation, then activate it
    embry0 progression-pack export [--default]     # the pack in effect (or the shipped default) as YAML

The verbs read like the settings file workflow (#162). ``check`` runs the
same checks a write meets and prints the same messages a 422 carries, one
per problem, as ``key: message``; only the database's own check of the
timezone needs the orchestrator. ``diff``, ``apply`` and ``export`` go
through the API like ``embry0 onboard``. ``apply`` shows the diff first,
sends nothing more when the file is the pack already in effect (so it makes
no version), and names the version it diffed against in ``If-Match``, so a
pack someone changed in between is refused rather than overwritten.

Exit codes: 0 done (or valid, or nothing to change); 1 the pack has
problems, the pack changed since the diff, or the confirmation was declined;
2 the orchestrator could not be reached or refused the key, or ``apply`` ran
without a terminal and without ``--yes``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx

from embry0.services.progression_pack import MAX_TEXT_BYTES, Problem
from embry0.services.progression_pack_store import pack_warnings, validate_text

_PACK = "/api/v1/progression/pack"


class _Exit(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _read(path: str) -> str:
    """The file's text; exit 1 when it cannot be read or is past the pack's limit."""
    file = Path(path)
    try:
        size = file.stat().st_size
        if size > MAX_TEXT_BYTES:
            _err(f"{path}: the pack is larger than {MAX_TEXT_BYTES // 1024} KiB")
            raise _Exit(1)
        return file.read_text(encoding="utf-8")
    except OSError as exc:
        _err(f"{path}: cannot be read ({exc.strerror or exc})")
        raise _Exit(1) from None
    except UnicodeDecodeError:
        _err(f"{path}: the pack must be UTF-8 text")
        raise _Exit(1) from None


def _problems(detail: Any) -> list[str]:
    """A 422's detail as the lines ``check`` prints: ``key: message``, one per problem."""
    if isinstance(detail, str):
        return [detail]
    if not isinstance(detail, list):
        return ["the pack was refused"]
    lines: list[str] = []
    for item in detail:
        if isinstance(item, dict) and isinstance(item.get("msg"), str):
            loc = tuple(part for part in item.get("loc") or [] if isinstance(part, str | int))
            lines.append(str(Problem(loc, str(item.get("type", "")), item["msg"])))
    return lines or ["the pack was refused"]


def _send(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request; a 401, any refusal but a 422 or 409, and an unreachable orchestrator end the command."""
    try:
        response = client.request(method, path, **kwargs)
    except httpx.TransportError:
        _err(f"cannot reach the orchestrator at {client.base_url} — is the stack running? (embry0 start)")
        raise _Exit(2) from None
    if response.status_code == 401:
        _err("the orchestrator rejected the request (401) — set EMBRY0_API_KEY or pass --api-key")
        raise _Exit(2)
    if response.status_code == 403:
        _err(f"refused (403): {_detail(response)} — writing the pack needs an admin key without a repository allowlist")
        raise _Exit(2)
    if response.status_code == 422:
        _err("the pack has problems:")
        for line in _problems(_json(response).get("detail")):
            _err(f"  {line}")
        raise _Exit(1)
    if response.status_code == 409:
        _err(f"{_detail(response)}")
        raise _Exit(1)
    if response.status_code >= 400:
        _err(f"the orchestrator refused the request ({response.status_code}): {_detail(response)}")
        raise _Exit(2)
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _detail(response: httpx.Response) -> str:
    detail = _json(response).get("detail")
    return detail if isinstance(detail, str) else response.text[:500]


def _dry_run(client: httpx.Client, text: str) -> dict[str, Any]:
    response = _send(
        client,
        "PUT",
        _PACK,
        params={"dry_run": 1, "source": "file"},
        content=text.encode("utf-8"),
        headers={"Content-Type": "application/yaml"},
    )
    return _json(response)


def _show(preview: dict[str, Any]) -> None:
    changes = bool(preview.get("would_change"))
    sections = preview.get("sections") or []
    print(f"would change: {'yes' if changes else 'no'}")
    if sections:
        print(f"sections: {', '.join(str(s) for s in sections)}")
    elif changes:
        print("sections: none; only comments or layout change")
    for warning in preview.get("warnings") or []:
        print(f"warning: {warning}")
    diff = preview.get("text_diff") or ""
    if diff:
        print(diff, end="" if diff.endswith("\n") else "\n")


def _check(args: argparse.Namespace) -> int:
    pack, problems = validate_text(_read(args.file))
    if pack is None:
        for problem in problems:
            _err(str(problem))
        return 1
    for warning in pack_warnings(pack):
        _err(f"warning: {warning}")
    print("the pack is valid")
    return 0


def _diff(args: argparse.Namespace, client: httpx.Client) -> int:
    _show(_dry_run(client, _read(args.file)))
    return 0


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        _err("refusing to apply without a terminal to confirm on; pass --yes")
        raise _Exit(2)
    answer = input("Activate this pack? It re-prices the whole history. [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _apply(args: argparse.Namespace, client: httpx.Client) -> int:
    text = _read(args.file)
    preview = _dry_run(client, text)
    _show(preview)
    if not preview.get("would_change"):
        print("nothing changes: this is the pack in effect, so no version is recorded")
        return 0
    if not _confirmed(args):
        _err("not applied")
        return 1
    params: dict[str, Any] = {"source": "file"}
    if args.reason:
        params["reason"] = args.reason
    response = _send(
        client,
        "PUT",
        _PACK,
        params=params,
        content=text.encode("utf-8"),
        # The version the diff above was taken against: a pack changed since is refused, not overwritten.
        headers={"Content-Type": "application/yaml", "If-Match": f'"{int(preview.get("version") or 0)}"'},
    )
    result = _json(response)
    if not result.get("changed"):
        print("nothing changes: this is the pack in effect, so no version is recorded")
        return 0
    previous = result.get("previous_version")
    print(f"activated version {result.get('version')}" + (f" (it replaces version {previous})" if previous else ""))
    return 0


def _export(args: argparse.Namespace, client: httpx.Client) -> int:
    path = f"{_PACK}/default" if args.default else _PACK
    response = _send(client, "GET", path, params={"format": "yaml"})
    text = response.text
    refused = response.headers.get("x-embry0-pack-refused")
    if refused:
        _err(
            f"warning: the stored version {refused} is refused by this release, so this is the shipped default in "
            f"effect; the refused text is at GET {_PACK}/versions/{refused}"
        )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def run(
    args: argparse.Namespace,
    *,
    base_url: str = "http://localhost:8200",
    api_key: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Run one verb; answers the exit code. ``transport`` stands in for the network in tests."""
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


def add_parser(subparsers: Any) -> None:
    """The ``progression-pack`` subcommand and its verbs."""
    parser = subparsers.add_parser(
        "progression-pack",
        help="Check, diff, apply or export the progression content pack (skills, rates, tiers, achievements...)",
    )
    verbs = parser.add_subparsers(dest="action", required=True)
    check = verbs.add_parser(
        "check", help="Validate a pack file offline, with the API's own checks; exit 1 on a problem"
    )
    check.add_argument("file", help="the pack, YAML or JSON")
    diff = verbs.add_parser("diff", help="Show what applying a pack file would change (a dry run; stores nothing)")
    diff.add_argument("file", help="the pack, YAML or JSON")
    apply = verbs.add_parser("apply", help="Show the diff, then activate a pack file (re-prices the whole history)")
    apply.add_argument("file", help="the pack, YAML or JSON")
    apply.add_argument("--yes", action="store_true", help="apply without asking (needed without a terminal)")
    apply.add_argument("--reason", default="", help="why, for the version history")
    export = verbs.add_parser("export", help="Write the pack in effect as YAML (stdout, or -o FILE)")
    export.add_argument("--default", action="store_true", help="the shipped default instead, comments included")
    export.add_argument("-o", "--output", default=None, help="write to this file instead of stdout")
    for verb in (diff, apply, export):
        verb.add_argument(
            "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
        )
        verb.add_argument(
            "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
        )

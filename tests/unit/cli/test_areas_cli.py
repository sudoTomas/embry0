"""`embry0 areas` (spec 2026-11-10): check a file offline; diff, apply and export a repository's areas through the API.

The API is faked at the transport, so each test sees exactly what the command sends (method, path, query,
headers, body) and the exit code it answers with: 0 done, 1 refused, 2 usage, 3 conflict, 4 unavailable.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

import httpx
import pytest

from embry0.cli.areas import run
from embry0.cli.exit_codes import OK, REFUSED, USAGE

GOOD = "# ours\nareas:\n  - name: Web\n    paths: [web/]\n    owners: ['@octo']\n"
BAD = "areas:\n  - name: Web\n  - name: Api\n    paths: [/api, ok/**]\n    owners: ['no way']\n  - name: web\n"
URL = "/api/v1/repos/group/sub/project/-/areas"


def _args(action: str, **kw: Any) -> Namespace:
    defaults = {"yes": False, "reason": "", "output": None, "repo": "group/sub/project"}
    return Namespace(action=action, **{**defaults, **kw})


class _Api:
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


def _file(tmp_path: Path, text: str, name: str = "areas.yaml") -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _stored(generation: int = 5) -> httpx.Response:
    return httpx.Response(200, json={"generation": generation}, headers={"ETag": f'"{generation}"'})


def _preview(would_change: bool = True) -> httpx.Response:
    changes = [{"area": "Web", "change": "added", "keys": ["name"], "owners_added": ["octo"], "owners_removed": []}]
    diff = "--- stored\n+++ proposed\n@@ -0,0 +1 @@\n+areas:\n"
    return httpx.Response(
        200, json={"would_change": would_change, "changes": changes if would_change else [], "text_diff": diff}
    )


def test_check_names_the_areas_of_a_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(_args("check", file=_file(tmp_path, GOOD))) == OK
    assert capsys.readouterr().out == "the areas document is valid; it names Web\n"


def test_check_lists_every_problem_by_key_and_echoes_no_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(_args("check", file=_file(tmp_path, BAD))) == REFUSED
    err = capsys.readouterr().err
    assert "areas.1.paths.0: A glob starts at the repository's root" in err
    assert "areas.1.owners.0: An owner is a login" in err
    assert "areas.2.name: This name labels work as area 'area:web'" in err
    assert "no way" not in err and "/api," not in err


def test_diff_previews_against_the_stored_generation_and_stores_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api(_stored(5), _preview())
    assert run(_args("diff", file=_file(tmp_path, GOOD)), transport=api.transport) == OK
    assert api.sent() == [("GET", URL, {}), ("PUT", URL, {"source": "file", "dry_run": "1"})]
    put = api.seen[1]
    assert put.headers["If-Match"] == '"5"' and put.headers["Content-Type"] == "application/yaml"
    assert put.content.decode() == GOOD, "the file is sent as written, comments included"
    out = capsys.readouterr().out
    assert "  Web: added; makes octo owner(s)" in out and "+areas:" in out


def test_apply_shows_the_diff_then_stores_with_the_generation_it_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _Api(_stored(5), _preview(), httpx.Response(200, json={"generation": 6, "revision_id": 41}))
    code = run(_args("apply", file=_file(tmp_path, GOOD), yes=True, reason="from our repo"), transport=api.transport)
    assert code == OK
    assert api.sent()[-1] == ("PUT", URL, {"source": "file", "reason": "from our repo"})
    assert api.seen[-1].headers["If-Match"] == '"5"'
    assert "stored: generation 6, change 41 in the repository's history" in capsys.readouterr().out


def test_apply_of_the_stored_text_writes_nothing(tmp_path: Path) -> None:
    api = _Api(_stored(5), _preview(would_change=False))
    assert run(_args("apply", file=_file(tmp_path, GOOD), yes=True), transport=api.transport) == OK
    assert [method for method, *_ in api.sent()] == ["GET", "PUT"], "only the dry run was sent"


def test_a_json_file_is_sent_as_json(tmp_path: Path) -> None:
    api = _Api(_stored(0), _preview())
    path = _file(tmp_path, '{"areas": [{"name": "Web"}]}', name="areas.json")
    assert run(_args("diff", file=path), transport=api.transport) == OK
    assert api.seen[1].headers["Content-Type"] == "application/json"


def test_export_writes_the_stored_text_as_written(tmp_path: Path) -> None:
    api = _Api(httpx.Response(200, text=GOOD, headers={"Content-Type": "application/yaml"}))
    out = tmp_path / "out.yaml"
    assert run(_args("export", output=str(out)), transport=api.transport) == OK
    assert api.sent() == [("GET", URL, {"format": "yaml"})]
    assert out.read_text() == GOOD


@pytest.mark.parametrize(
    "repo",
    ["acme/web?format=json", "acme/web#x", "acme web/x", "acme", "acme/../web", "/acme/web", "acme/web\n", "acme//web"],
)
@pytest.mark.parametrize("action", ["diff", "apply", "export"])
def test_a_repository_that_is_not_one_is_a_usage_error_and_nothing_is_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], action: str, repo: str
) -> None:
    """#462's review: ``export 'acme/web?format=json'`` fetched the repository's own record and exited 0."""
    api = _Api(_stored(), _preview(), httpx.Response(200, text=GOOD))
    code = run(_args(action, repo=repo, file=_file(tmp_path, GOOD), yes=True), transport=api.transport)
    assert (code, api.sent()) == (USAGE, []), f"{action} {repo!r} answered {code} after sending {api.sent()}"
    assert "group/name" in capsys.readouterr().err


def test_a_repository_s_segments_are_sent_as_written(tmp_path: Path) -> None:
    api = _Api(httpx.Response(200, text=GOOD))
    assert run(_args("export", repo="acme/.github", output=str(tmp_path / "o.yaml")), transport=api.transport) == OK
    assert [r.url.raw_path for r in api.seen] == [b"/api/v1/repos/acme/.github/-/areas?format=yaml"]


def test_each_segment_of_the_route_is_quoted() -> None:
    from embry0.cli.areas import _path

    assert _path("acme/a b?c#d") == "/api/v1/repos/acme/a%20b%3Fc%23d/-/areas", "a segment is sent as one segment"

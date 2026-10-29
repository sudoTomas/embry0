"""`embry0 settings --org` and `--repo`: an organization's or a repository's layer (spec 2026-10-29, #161).

The API is faked at the transport, as in ``test_settings_cli.py``, so each
test sees the query that names the layer.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from embry0.cli.exit_codes import OK, REFUSED, USAGE
from embry0.cli.settings import run
from tests.unit.cli.test_settings_cli import URL, _Api, _args, _file, _preview, _stored

HOURS = "# office hours\nasks:\n  hours: '08-17'\n"


def test_diff_apply_export_and_revisions_name_the_layer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    diff = _Api(_preview())
    assert run(_args("diff", file=_file(tmp_path, HOURS), org="acme"), transport=diff.transport) == OK
    written = httpx.Response(200, json={"generation": 1, "revision_id": 3, "changes": []})
    apply = _Api(_stored(0), _preview(), written)
    args = _args("apply", file=_file(tmp_path, HOURS), repo="acme/widgets", yes=True)
    assert run(args, transport=apply.transport) == OK
    export = _Api(httpx.Response(200, text=""))
    assert run(_args("export", org="acme"), transport=export.transport) == OK
    revisions = _Api(httpx.Response(200, json={"revisions": []}))
    assert run(_args("revisions", repo="acme/widgets", limit=5), transport=revisions.transport) == OK
    assert diff.sent() == [("PUT", URL, {"dry_run": "1", "source": "file", "owner": "acme"})], (
        "diff --org did not name the organization's layer, so it diffed the deployment's document"
    )
    assert apply.sent() == [
        ("GET", URL, {"repo": "acme/widgets"}),
        ("PUT", URL, {"dry_run": "1", "source": "file", "repo": "acme/widgets"}),
        ("PUT", URL, {"source": "file", "repo": "acme/widgets"}),
    ], "apply --repo must read, diff and write the repository's layer"
    assert apply.seen[2].headers.get("If-Match") == '"0"', "a layer nobody wrote is created against generation 0"
    assert export.sent() == [("GET", URL, {"format": "yaml", "owner": "acme"})]
    assert revisions.sent() == [("GET", f"{URL}/revisions", {"limit": "5", "repo": "acme/widgets"})]
    assert "every key takes what it inherits" in capsys.readouterr().err


def test_check_refuses_a_key_the_layer_may_not_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    both = _file(tmp_path, "asks:\n  enabled: true\n  hours: '08-17'\n")
    assert run(_args("check", file=both, org="acme")) == REFUSED, "an organization may not turn asks on"
    err = capsys.readouterr().err
    assert "asks.enabled: An organization cannot set 'enabled'." in err
    assert "asks.hours" not in err, "a key the organization may set is not a problem"
    assert run(_args("check", file=_file(tmp_path, HOURS), org="acme")) == OK
    assert "the settings organization layer is valid; it sets asks.hours" in capsys.readouterr().out
    assert run(_args("check", file=_file(tmp_path, HOURS), repo="acme/widgets")) == REFUSED
    assert "asks.hours: A repository cannot set anything in 'asks'." in capsys.readouterr().err
    assert run(_args("check", file=both)) == OK, "the deployment's document sets every key"


def test_import_env_and_export_default_are_the_deployment_s_only(capsys: pytest.CaptureFixture[str]) -> None:
    api = _Api()
    assert run(_args("import-env", org="acme"), transport=api.transport) == USAGE
    assert "the environment is the deployment's" in capsys.readouterr().err
    assert run(_args("export", default=True, repo="acme/widgets"), transport=api.transport) == USAGE
    assert api.seen == [], "nothing is sent"


def test_org_and_repo_are_one_or_the_other(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import embry0.cli.__main__ as cli

    argv = ["embry0", "settings", "diff", "--org", "acme", "--repo", "acme/widgets", _file(tmp_path, HOURS)]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as usage:
        cli.main()
    assert usage.value.code == USAGE
    monkeypatch.setattr("sys.argv", ["embry0", "settings", "check", "--org", "acme", _file(tmp_path, HOURS)])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == OK, "--org reaches the check through the real parser"

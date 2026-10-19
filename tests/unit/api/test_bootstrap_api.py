"""POST /api/v1/repos/bootstrap endpoint tests (EMB-49)."""

from __future__ import annotations

import pytest
import yaml

from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_repo_shape(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "not-a-repo"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_app_slug(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "a/b", "app": "Bad App"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_rejects_unknown_field(api_client) -> None:
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "a/b", "nope": 1})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bootstrap_happy_path_writes_db(api_client, monkeypatch) -> None:
    """RAV-1251: bootstrap writes the DB config + registers the repo."""

    async def _fake_verify(repo, token, credential_resolver=None):
        return "main", True

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake_verify)
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["qa_required"] == "never"
    assert body["qa_config_path"] == "db:repo_qa_configs/acme/fresh"
    # The qa-config GET serves the stored document.
    r2 = await api_client.get("/api/v1/repos/acme/fresh/qa-config")
    assert r2.status_code == 200
    assert "version: 2" in r2.text
    # And the repo is registered.
    r3 = await api_client.get("/api/v1/repos")
    repos = {row["repo"]: row for row in r3.json()}
    assert repos["acme/fresh"]["has_qa_config"] is True


@pytest.mark.asyncio
async def test_bootstrap_conflict_maps_to_400(api_client, monkeypatch) -> None:
    async def _fake_verify(repo, token, credential_resolver=None):
        return "main", True

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake_verify)
    assert (await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})).status_code == 200
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/fresh"})
    assert r.status_code == 400
    assert r.json()["detail"].startswith("This repository already has a test setup.")


async def _verified(repo, token, credential_resolver=None):
    return "main", True


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["frontend_url", "ready_check_url"])
async def test_bootstrap_asks_for_a_scheme_on_an_address(api_client, monkeypatch, field) -> None:
    """An address without http:// used to reach the starter's own parse and
    surface as a 500."""
    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _verified)
    r = await api_client.post(
        "/api/v1/repos/bootstrap", json={"repo": "acme/widgets", "app": "web", field: "localhost:3000"}
    )
    assert r.status_code == 422, r.text
    assert "Start the address with http:// or https://" in r.text
    assert (await api_client.get("/api/v1/repos/acme/widgets/qa-config")).status_code == 404


@pytest.mark.asyncio
async def test_bootstrap_stores_a_boot_command_with_quotes(api_client, monkeypatch) -> None:
    """A double quote in the boot command used to break the starter's YAML."""
    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _verified)
    boot = 'npm run dev -- --host "0.0.0.0"'
    r = await api_client.post(
        "/api/v1/repos/bootstrap", json={"repo": "acme/widgets", "app": "web", "boot_command": boot}
    )
    assert r.status_code == 200, r.text
    stored = await api_client.get("/api/v1/repos/acme/widgets/qa-config")
    cfg = parse_qa_yaml_v2(stored.text)
    assert cfg.apps["web"].boot_command == boot
    assert cfg.defaults.sandbox_profile == "qa-external"


def _unreadable_yaml(*args, **kwargs):
    return parse_qa_yaml_v2("version: 2\napps: [\n")


def _invalid_setup(*args, **kwargs):
    return parse_qa_yaml_v2("version: 2\napps:\n  web:\n    boot_comand: npm run dev\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("render", [_unreadable_yaml, _invalid_setup])
async def test_bootstrap_answers_422_when_the_setup_cannot_be_read(api_client, monkeypatch, render) -> None:
    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _verified)
    monkeypatch.setattr("embry0.services.bootstrap.render_starter_qa_yaml", render)
    r = await api_client.post("/api/v1/repos/bootstrap", json={"repo": "acme/widgets", "app": "web"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith("That setup could not be read: ")
    assert "\n" not in detail
    assert (await api_client.get("/api/v1/repos/acme/widgets/qa-config")).status_code == 404


def test_the_unreadable_fixtures_raise_what_the_endpoint_maps():
    from pydantic import ValidationError

    with pytest.raises(yaml.YAMLError):
        _unreadable_yaml()
    with pytest.raises(ValidationError):
        _invalid_setup()

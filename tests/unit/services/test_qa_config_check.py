"""check_qa_config: the free check that catches setups every test run would stop on."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services import qa_config_check
from embry0.services.qa_config_check import check_qa_config

_PROFILES = {
    "slim": {"name": "slim", "base_image": "embry0-sandbox:latest"},
    "qa-external": {"name": "qa-external", "base_image": "registry:5000/embry0-sandbox-qa:latest"},
    "qa-docker": {"name": "qa-docker", "base_image": "embry0-sandbox-qa:latest", "dind_enabled": True},
}


class _Profiles:
    async def get(self, name: str) -> dict[str, Any] | None:
        return _PROFILES.get(name)


@pytest.fixture(autouse=True)
def _installed_providers(monkeypatch):
    monkeypatch.setattr(qa_config_check, "available_provider_names", lambda: ["npm-workspaces-turbo", "static-apps"])


def _setup(
    *,
    profile: str = "qa-external",
    provider: str = "npm-workspaces-turbo",
    url: str = "http://localhost:5173",
    extra_defaults: str = "",
    extra_app: str = "",
    qa_required: str = "auto",
) -> str:
    return (
        f"version: 2\nqa_required: {qa_required}\nworkspace_provider:\n  type: {provider}\n"
        f"defaults:\n  sandbox_profile: {profile}\n  acceptance_criteria_template:\n"
        f"    - The home page loads\n    - Sign in works\n{extra_defaults}"
        f"apps:\n  web:\n    boot_command: npm run dev\n    frontend_url: {url}\n"
        f"    ready_checks:\n      - http: {url}\n{extra_app}"
    )


async def _check(text: str, *, env_keys: set[str] | None = None, docker_access: bool = False) -> dict[str, Any]:
    return await check_qa_config(
        text, profiles_repo=_Profiles(), env_keys=env_keys or set(), docker_access=docker_access
    )


def _codes(result: dict[str, Any]) -> list[str]:
    return [w["code"] for w in result["warnings"]]


async def test_a_browser_machine_passes():
    result = await _check(_setup())
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["warnings"] == []


async def test_a_machine_without_a_browser_stops_every_run():
    result = await _check(_setup(profile="slim"))
    assert result["ok"] is False
    assert result["problems"] == [
        {
            "where": "app web",
            "plain": "The work machine 'slim' has no browser, so every test run would stop.",
            "fix": "Use 'qa-external'.",
        }
    ]


async def test_a_machine_that_does_not_exist_is_a_problem():
    result = await _check(_setup(profile="does-not-exist"))
    assert result["ok"] is False
    assert result["problems"] == [
        {
            "where": "app web",
            "plain": "The work machine 'does-not-exist' does not exist on this embry0.",
            "fix": "Use 'qa-external'.",
        }
    ]


async def test_two_apps_on_one_missing_machine_give_one_problem():
    text = _setup(profile="slim") + "  api:\n    boot_command: npm start\n    frontend_url: http://localhost:4000\n"
    text += "    ready_checks:\n      - http: http://localhost:4000\n"
    result = await _check(text)
    assert [p["where"] for p in result["problems"]] == ["app web, app api"]


async def test_a_way_to_find_apps_that_is_not_installed_is_a_problem():
    result = await _check(_setup(provider="maven"))
    assert result["ok"] is False
    assert result["problems"] == [
        {
            "where": "workspace_provider",
            "plain": "'maven' is not a way embry0 knows to find your apps.",
            "fix": "Use one of: npm-workspaces-turbo, static-apps.",
        }
    ]


async def test_an_app_with_no_ready_check_is_a_problem():
    text = (
        "version: 2\nworkspace_provider:\n  type: npm-workspaces-turbo\ndefaults:\n  sandbox_profile: qa-external\n"
        "apps:\n  web:\n    boot_command: npm run dev\n    frontend_url: http://localhost:5173\n"
    )
    result = await _check(text)
    assert result["ok"] is False
    assert result["problems"] == [
        {
            "where": "app web",
            "plain": "embry0 cannot tell when web is ready: it has no ready check.",
            "fix": "Add an address to check under ready_checks.",
        }
    ]
    assert result["summary"]["apps"] == []


async def test_a_setup_that_cannot_be_read_is_explained_and_has_no_summary():
    text = _setup().replace("boot_command", "boot_comand")
    result = await _check(text)
    assert result["ok"] is False
    assert result["summary"] is None
    assert result["warnings"] == []
    assert result["problems"] == [
        {
            "where": "app web",
            "plain": "'boot_comand' is not a setting embry0 knows. Did you mean 'boot_command'? (app web)",
            "fix": "",
        }
    ]


async def test_a_tab_indent_is_a_problem_on_its_line():
    result = await _check("version: 2\napps:\n\tweb:\n    boot_command: x\n")
    assert result["problems"] == [
        {"where": "line 3", "plain": "Line 3 starts with a tab. Indent with spaces.", "fix": ""}
    ]


async def test_a_managed_app_on_a_real_site_must_be_confirmed():
    result = await _check(_setup(url="https://www.example.com"))
    assert result["ok"] is True
    assert result["warnings"] == [
        {
            "code": "live_address",
            "plain": (
                "web is tested at https://www.example.com, which is outside embry0's test computer. "
                "Tests there click through a real site."
            ),
            "confirm": True,
        }
    ]


async def test_a_local_address_is_not_a_live_one():
    assert "live_address" not in _codes(await _check(_setup(url="http://localhost:5173")))


async def test_a_container_address_in_docker_mode_is_not_a_live_one():
    """In mode dind the address names a container on the test computer."""
    text = _setup(url="http://web:3000", extra_defaults="  mode: dind\n")
    assert "live_address" not in _codes(await _check(text, docker_access=True))


async def test_a_deployed_app_without_guardrails_warns_twice():
    text = (
        "version: 2\ndefaults:\n  sandbox_profile: qa-external\napps:\n  web:\n    target: deployed\n"
        "    frontend_url: https://staging.example.com\n    ready_checks:\n      - http: https://staging.example.com\n"
    )
    result = await _check(text)
    assert result["ok"] is True
    assert result["warnings"] == [
        {
            "code": "deployed_target",
            "plain": "web is tested where it already runs, at https://staging.example.com. Tests there use real data.",
            "confirm": True,
        },
        {
            "code": "deployed_without_guardrails",
            "plain": (
                "No rule tells the tests what not to touch on web. "
                "They could press Send, Save or Delete on the running site."
            ),
            "confirm": True,
        },
    ]


async def test_a_deployed_app_with_guardrails_warns_once():
    text = (
        "version: 2\ndefaults:\n  sandbox_profile: qa-external\n  guardrails:\n    - Never press Send\n"
        "apps:\n  web:\n    target: deployed\n    frontend_url: https://staging.example.com\n"
        "    ready_checks:\n      - http: https://staging.example.com\n"
    )
    assert _codes(await _check(text)) == ["deployed_target"]


async def test_docker_mode_without_the_grant_must_be_confirmed():
    text = _setup(url="http://web:3000", extra_defaults="  mode: dind\n")
    result = await _check(text, docker_access=False)
    assert result["warnings"] == [
        {
            "code": "needs_docker",
            "plain": "Testing will not start until an admin grants Docker access.",
            "confirm": True,
        }
    ]
    assert "needs_docker" not in _codes(await _check(text, docker_access=True))


async def test_a_machine_that_asks_for_docker_needs_the_grant_in_process_mode_too():
    """The sandbox is refused on the machine's own ask (sandbox_manager.py)."""
    assert _codes(await _check(_setup(profile="qa-docker"))) == ["needs_docker"]
    assert _codes(await _check(_setup(profile="qa-docker"), docker_access=True)) == []


async def test_testing_off_is_said_without_a_confirm():
    result = await _check(_setup(qa_required="never"))
    assert result["warnings"] == [
        {
            "code": "testing_off",
            "plain": "Testing stays off: embry0 opens pull requests without testing the app.",
            "confirm": False,
        }
    ]


async def test_a_sign_in_value_with_no_value_yet_is_said():
    auth = "  auth:\n    storage_state_from:\n      secret: QA_LOGIN_STATE\n"
    result = await _check(_setup(extra_defaults=auth))
    assert result["warnings"] == [
        {
            "code": "login_value_missing",
            "plain": "The setup signs in with QA_LOGIN_STATE, which has no value yet.",
            "confirm": False,
        }
    ]
    assert result["ok"] is True
    assert _codes(await _check(_setup(extra_defaults=auth), env_keys={"QA_LOGIN_STATE"})) == []


async def test_the_summary_says_what_starts_where_and_what_is_checked():
    result = await _check(_setup(extra_app="    guardrails:\n      - Never press Delete\n"))
    assert result["summary"] == {
        "when": "auto",
        "when_plain": "When a change touches the app",
        "apps": [
            {
                "name": "web",
                "target": "managed",
                "start": "npm run dev",
                "address": "http://localhost:5173",
                "address_is_local": True,
                "criteria": ["The home page loads", "Sign in works"],
                "guardrails": 1,
                "machine": "qa-external",
                "machine_has_browser": True,
            }
        ],
    }


async def test_the_summary_marks_a_machine_without_a_browser():
    result = await _check(_setup(profile="slim", qa_required="always"))
    assert result["summary"]["when_plain"] == "On every change"
    assert result["summary"]["apps"][0]["machine_has_browser"] is False

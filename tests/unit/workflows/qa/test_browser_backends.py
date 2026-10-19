"""Browser-backend registry (#230): playwright entry, lookup, immutability.

The EXPECTED_* literals below are the pre-registry wiring copied verbatim
from the old duplicated seeds (plus browser_find, added by #81) — they are
the byte-identical regression guard. Do not derive them from the registry;
their whole point is to be an independent copy.
"""

import pytest

from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.workflows.qa.agent_seed import QA_AGENT_SEED
from embry0.workflows.qa.browser_backends import (
    DEFAULT_BROWSER_BACKEND,
    PLAYWRIGHT,
    backend_for,
    backend_names,
)

EXPECTED_PLAYWRIGHT_TOOLS = [
    "mcp__playwright__browser_navigate",
    "mcp__playwright__browser_navigate_back",
    "mcp__playwright__browser_click",
    "mcp__playwright__browser_type",
    "mcp__playwright__browser_press_key",
    "mcp__playwright__browser_snapshot",
    "mcp__playwright__browser_find",
    "mcp__playwright__browser_take_screenshot",
    "mcp__playwright__browser_console_messages",
    "mcp__playwright__browser_network_requests",
    "mcp__playwright__browser_network_request",
    "mcp__playwright__browser_wait_for",
    "mcp__playwright__browser_resize",
    "mcp__playwright__browser_evaluate",
    "mcp__playwright__browser_hover",
    "mcp__playwright__browser_fill_form",
    "mcp__playwright__browser_select_option",
    "mcp__playwright__browser_drag",
    "mcp__playwright__browser_drop",
    "mcp__playwright__browser_handle_dialog",
    "mcp__playwright__browser_file_upload",
    "mcp__playwright__browser_close",
    "mcp__playwright__browser_tabs",
]

EXPECTED_PLAYWRIGHT_MCP_SERVERS = {
    "playwright": {
        "type": "stdio",
        "command": "playwright-mcp",
        "args": ["--headless", "--browser", "chromium"],
    }
}


def test_registry_lookup():
    assert backend_for("playwright") is PLAYWRIGHT
    assert DEFAULT_BROWSER_BACKEND == "playwright"
    assert "playwright" in backend_names()


def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="unknown browser backend 'netscape'"):
        backend_for("netscape")


def test_playwright_wiring_is_byte_identical_to_pre_registry_seed():
    """Regression guard for #230: extraction changed nothing observable."""
    assert PLAYWRIGHT.browser_tools() == EXPECTED_PLAYWRIGHT_TOOLS
    assert PLAYWRIGHT.mcp_servers() == EXPECTED_PLAYWRIGHT_MCP_SERVERS


def test_seed_derives_from_registry_byte_identically():
    """The QA seed's browser slice equals the historical literal wiring."""
    assert QA_AGENT_SEED["tools"] == ["Read", "Glob", "Grep", "Bash", "Edit", *EXPECTED_PLAYWRIGHT_TOOLS]
    assert QA_AGENT_SEED["mcp_servers"] == EXPECTED_PLAYWRIGHT_MCP_SERVERS
    assert BUILTIN_SEED["qa"]["tools"] == QA_AGENT_SEED["tools"]
    assert BUILTIN_SEED["qa"]["mcp_servers"] == QA_AGENT_SEED["mcp_servers"]


def test_accessors_return_fresh_copies():
    """Definitions built from the registry must never share mutable state."""
    a, b = PLAYWRIGHT.mcp_servers(), PLAYWRIGHT.mcp_servers()
    assert a == b
    assert a is not b
    assert a["playwright"] is not b["playwright"]
    assert a["playwright"]["args"] is not b["playwright"]["args"]
    t1, t2 = PLAYWRIGHT.browser_tools(), PLAYWRIGHT.browser_tools()
    assert t1 == t2
    assert t1 is not t2


def test_storage_state_environ():
    env = PLAYWRIGHT.storage_state_environ("/workspace/.qa/storage-state.json")
    assert env == {
        "PLAYWRIGHT_MCP_STORAGE_STATE": "/workspace/.qa/storage-state.json",
        "PLAYWRIGHT_MCP_ISOLATED": "1",
    }


def test_playwright_grants_browser_find_and_keeps_the_exclusions_out():
    """#81: the agent reaches for browser_find on every QA run; the two
    deliberately excluded tools must never creep in silently."""
    from embry0.workflows.qa.browser_backends import PLAYWRIGHT_EXCLUDED_TOOLS

    assert "mcp__playwright__browser_find" in PLAYWRIGHT.tool_names
    assert PLAYWRIGHT_EXCLUDED_TOOLS == {
        "mcp__playwright__browser_run_code_unsafe",
        "mcp__playwright__browser_emulate_media",
    }
    assert PLAYWRIGHT_EXCLUDED_TOOLS.isdisjoint(PLAYWRIGHT.tool_names)


def test_backend_dataclass_is_immutable():
    with pytest.raises(Exception):
        PLAYWRIGHT.name = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        PLAYWRIGHT.storage_state_env["PLAYWRIGHT_MCP_ISOLATED"] = "0"  # type: ignore[index]


# -------- vibium entry + with_browser_backend (#233) --------

EXPECTED_VIBIUM_TOOL_COUNT = 49


def test_vibium_registry_entry():
    from embry0.workflows.qa.browser_backends import VIBIUM

    assert backend_for("vibium") is VIBIUM
    assert len(VIBIUM.tool_names) == EXPECTED_VIBIUM_TOOL_COUNT
    assert len(set(VIBIUM.tool_names)) == EXPECTED_VIBIUM_TOOL_COUNT  # no dupes
    assert all(t.startswith("mcp__vibium__browser_") for t in VIBIUM.tool_names)
    assert VIBIUM.mcp_servers() == {
        "vibium": {
            "type": "stdio",
            "command": "vibium",
            "args": ["mcp", "--headless", "--screenshot-dir", "/workspace/.qa/screenshots"],
        }
    }
    assert dict(VIBIUM.env) == {"VIBIUM_CHROME_ARGS": "--no-sandbox"}
    # Restore is a TOOL CALL — no env contract beyond the generic path var.
    assert VIBIUM.storage_state_environ("/x") == {}


def test_vibium_excludes_the_dangerous_tools():
    """The spike's deliberate exclusions must never creep in silently."""
    from embry0.workflows.qa.browser_backends import VIBIUM

    banned = {
        "mcp__vibium__browser_set_content",  # rewrites the page under test
        "mcp__vibium__browser_storage_state",  # session-state exfiltration
        "mcp__vibium__browser_get_cookies",
        "mcp__vibium__browser_set_cookie",
        "mcp__vibium__browser_delete_cookies",
        "mcp__vibium__browser_sleep",  # flaky timing
        "mcp__vibium__page_clock_install",  # clock mocking fakes criteria
        "mcp__vibium__browser_mouse_move",  # bypasses actionability
    }
    assert banned.isdisjoint(VIBIUM.tool_names)


def test_with_browser_backend_playwright_is_byte_identical_noop():
    """Acceptance criterion #233: a playwright job's definition is exactly
    the stored seed — the merge must not perturb a single byte."""
    from embry0.workflows.qa.browser_backends import with_browser_backend

    merged = with_browser_backend(QA_AGENT_SEED, PLAYWRIGHT)
    assert merged == QA_AGENT_SEED
    assert merged is not QA_AGENT_SEED  # still a copy, never the seed itself


def test_with_browser_backend_vibium_swaps_browser_wiring():
    from embry0.workflows.qa.browser_backends import VIBIUM, with_browser_backend

    merged = with_browser_backend(QA_AGENT_SEED, VIBIUM)
    # Base tools survive, in order, ahead of the backend tools.
    assert merged["tools"][:5] == ["Read", "Glob", "Grep", "Bash", "Edit"]
    assert merged["tools"][5:] == list(VIBIUM.tool_names)
    assert not any(t.startswith("mcp__playwright__") for t in merged["tools"])
    assert merged["mcp_servers"] == VIBIUM.mcp_servers()
    # Everything non-browser is untouched (system_prompt gains exactly the
    # backend fragment — asserted in the prompt-fragment test).
    for key in ("description", "model", "skills", "execution_mode", "auth_mode"):
        assert merged[key] == QA_AGENT_SEED[key]
    assert merged["system_prompt"] == QA_AGENT_SEED["system_prompt"] + VIBIUM.prompt_fragment
    # The seed itself was not mutated.
    assert "mcp__playwright__browser_navigate" in QA_AGENT_SEED["tools"]
    assert "playwright" in QA_AGENT_SEED["mcp_servers"]


def test_with_browser_backend_preserves_foreign_mcp_tools_and_servers():
    """Acceptance criterion #233: non-browser MCP grants (e.g. the
    invocation-scoped context server) survive the merge unchanged."""
    from embry0.workflows.qa.browser_backends import VIBIUM, with_browser_backend

    definition = dict(QA_AGENT_SEED)
    definition["tools"] = [*QA_AGENT_SEED["tools"], "mcp__embry0_context__log_query"]
    definition["mcp_servers"] = {
        **QA_AGENT_SEED["mcp_servers"],
        "embry0_context": {"type": "stdio", "command": "ctx", "args": []},
    }
    merged = with_browser_backend(definition, VIBIUM)
    assert "mcp__embry0_context__log_query" in merged["tools"]
    assert merged["mcp_servers"]["embry0_context"] == {"type": "stdio", "command": "ctx", "args": []}
    assert "playwright" not in merged["mcp_servers"]
    assert "vibium" in merged["mcp_servers"]


def test_with_browser_backend_appends_prompt_fragment():
    import dataclasses

    from embry0.workflows.qa.browser_backends import VIBIUM, with_browser_backend

    fragmented = dataclasses.replace(VIBIUM, prompt_fragment="\n\nFRAGMENT")
    merged = with_browser_backend(QA_AGENT_SEED, fragmented)
    assert merged["system_prompt"] == QA_AGENT_SEED["system_prompt"] + "\n\nFRAGMENT"
    # An empty fragment leaves the prompt untouched.
    empty = dataclasses.replace(VIBIUM, prompt_fragment="")
    merged2 = with_browser_backend(QA_AGENT_SEED, empty)
    assert merged2["system_prompt"] == QA_AGENT_SEED["system_prompt"]
    # Vibium's real fragment (#234) rides the merge verbatim.
    merged3 = with_browser_backend(QA_AGENT_SEED, VIBIUM)
    assert merged3["system_prompt"] == QA_AGENT_SEED["system_prompt"] + VIBIUM.prompt_fragment


# -------- vibium prompt fragment content (#234) --------


def test_vibium_fragment_auth_protocol_navigate_restore_reload():
    """Decision 6 as corrected 2026-08-29: web-storage restore only works
    on-origin (verified on the pinned 26.8.21 binary), so the mandated
    order is start -> navigate -> restore($QA_STORAGE_STATE_PATH) ->
    navigate again; manual login stays forbidden on a rejected session."""
    from embry0.workflows.qa.browser_backends import VIBIUM

    f = VIBIUM.prompt_fragment
    assert "$QA_STORAGE_STATE_PATH" in f
    # The four steps appear in the mandated order.
    i_start = f.index("1. browser_start")
    i_nav = f.index("2. browser_navigate")
    i_restore = f.index("3. browser_restore_storage")
    i_reload = f.index("4. browser_navigate")
    assert i_start < i_nav < i_restore < i_reload
    assert "NOT applied automatically" in f
    assert "never improvise credentials" in f
    # The corrected protocol's key warnings.
    flat = " ".join(f.split())
    assert "ON the app's origin" in flat
    assert "do not judge auth yet" in flat


def test_vibium_fragment_console_network_shim():
    """Decision 2: evaluate-shim covers the missing console/network tools —
    installed after EVERY navigation, read back via window.__qaRec, limits
    stated, and every shim API it uses is in the granted toolset."""
    from embry0.workflows.qa.browser_backends import VIBIUM

    f = VIBIUM.prompt_fragment
    flat = " ".join(f.split())  # collapse line wraps for phrase assertions
    assert "NO console/network tools" in flat
    assert "window.__qaRec" in f
    assert "after EVERY navigation" in flat
    assert "unhandledrejection" in f
    assert "XMLHttpRequest" in f
    assert "websockets, workers" in f  # honesty about shim limits
    # The shim rides browser_evaluate — which must be granted.
    assert "mcp__vibium__browser_evaluate" in VIBIUM.tool_names


def test_vibium_fragment_trace_recording():
    from embry0.workflows.qa.browser_backends import VIBIUM

    f = VIBIUM.prompt_fragment
    assert "browser_record_start" in f
    assert "browser_record_stop" in f
    assert "/workspace/.qa/traces/session.zip" in f
    assert 'under "traces/session.zip"' in f  # existing traces artifact kind
    assert "Playwright trace" in f  # viewer compatibility


def test_vibium_fragment_targeting_and_engine():
    from embry0.workflows.qa.browser_backends import VIBIUM

    f = VIBIUM.prompt_fragment
    assert "browser_map" in f
    assert "browser_a11y_tree" in f
    assert "engine: firefox" in f  # explicit prohibition
    # Tool-name mapping covers every playwright tool the base prompt names.
    for pw_tool in (
        "browser_take_screenshot",
        "browser_snapshot",
        "browser_console_messages",
        "browser_network_requests",
        "browser_wait_for",
    ):
        assert pw_tool in f, f"fragment must map {pw_tool} to a vibium equivalent"


def test_vibium_fragment_only_names_granted_tools():
    """Every browser_* tool the fragment tells the agent to call must be in
    the allowlist — a fragment naming an ungranted tool sends the agent
    into guaranteed denials."""
    import re

    from embry0.workflows.qa.browser_backends import VIBIUM

    granted = {t.removeprefix("mcp__vibium__") for t in VIBIUM.tool_names}
    # Tools the fragment PRESCRIBES for this backend (the playwright names in
    # the mapping table document what they replace and are naturally absent).
    playwright_names = {
        "browser_take_screenshot",
        "browser_snapshot",
        "browser_console_messages",
        "browser_network_requests",
        "browser_wait_for",
    }
    named = set(re.findall(r"browser_[a-z0-9_]+", VIBIUM.prompt_fragment)) - playwright_names
    ungranted = named - granted
    assert not ungranted, f"fragment names ungranted tools: {sorted(ungranted)}"


def test_vibium_auth_corpus_fixture_parses_and_resolves():
    """#234 acceptance: corpus covers browser_backend: vibium + auth."""
    from pathlib import Path

    from embry0.workflows.qa.qa_yaml_resolve import resolve_app_config
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    fixture = Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2" / "managed-vibium-with-auth.yaml"
    cfg = parse_qa_yaml_v2(fixture.read_text())
    assert cfg.defaults.browser_backend == "vibium"
    resolved = resolve_app_config("web", cfg, app_local=None)
    assert resolved.browser_backend == "vibium"
    assert resolved.auth is not None
    assert resolved.auth.storage_state_from.secret == "QA_STORAGE_STATE"
    assert resolved.target == "managed"


# -------- to_vibium_storage_state (#234) --------


def test_storage_state_conversion_basic():
    """Playwright shape -> vibium 26.8.21 native shape, verified against the
    binary's own export format."""
    from embry0.workflows.qa.browser_backends import to_vibium_storage_state

    pw = {
        "cookies": [{"name": "qa_session", "value": "tok", "domain": "localhost", "path": "/"}],
        "origins": [
            {
                "origin": "http://localhost:3000",
                "localStorage": [{"name": "qa_user", "value": "alex"}, {"name": "token", "value": "abc"}],
            }
        ],
    }
    out = to_vibium_storage_state(pw, "http://localhost:3000")
    assert out == {
        "cookies": [{"name": "qa_session", "value": "tok", "domain": "localhost", "path": "/"}],
        "storage": {
            "origin": "http://localhost:3000",
            "localStorage": {"qa_user": "alex", "token": "abc"},
            "sessionStorage": {},
        },
    }


def test_storage_state_conversion_picks_frontend_origin():
    """Multi-origin states keep the frontend_url match (native shape is
    single-origin); other origins are dropped."""
    from embry0.workflows.qa.browser_backends import to_vibium_storage_state

    pw = {
        "cookies": [],
        "origins": [
            {"origin": "https://auth.example.com", "localStorage": [{"name": "idp", "value": "x"}]},
            {"origin": "https://app.example.com", "localStorage": [{"name": "session", "value": "y"}]},
        ],
    }
    out = to_vibium_storage_state(pw, "https://app.example.com/dashboard")
    assert out["storage"]["origin"] == "https://app.example.com"
    assert out["storage"]["localStorage"] == {"session": "y"}


def test_storage_state_conversion_no_match_falls_back_to_first():
    from embry0.workflows.qa.browser_backends import to_vibium_storage_state

    pw = {
        "cookies": [],
        "origins": [{"origin": "https://other.example", "localStorage": [{"name": "k", "value": "v"}]}],
    }
    out = to_vibium_storage_state(pw, "https://app.example.com")
    assert out["storage"]["origin"] == "https://other.example"
    assert out["storage"]["localStorage"] == {"k": "v"}


def test_storage_state_conversion_cookies_only():
    """A cookies-only Playwright state (no origins) converts cleanly with an
    empty storage section anchored on the frontend origin."""
    from embry0.workflows.qa.browser_backends import to_vibium_storage_state

    pw = {"cookies": [{"name": "s", "value": "1", "domain": "x", "path": "/"}]}
    out = to_vibium_storage_state(pw, "http://localhost:8080/app")
    assert out["storage"] == {"origin": "http://localhost:8080", "localStorage": {}, "sessionStorage": {}}
    assert len(out["cookies"]) == 1

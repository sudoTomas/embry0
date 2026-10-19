"""Browser-backend registry for the QA agent.

A *browser backend* is the pluggable unit that supplies the QA agent's
browser (see docs/glossary.md#browser-backend). Each backend bundles:

  - an MCP stdio server config (how the browser server is launched),
  - the exact-name tool allowlist the agent is granted (the claude-agent-sdk
    treats ``tools`` as an exact-name allowlist; MCP tools surface as
    ``mcp__<server>__<tool>``, so every browser tool must be listed
    explicitly or the SDK silently rejects it),
  - backend-specific sandbox env vars,
  - the auth/storage-state env contract (how a pre-seeded login state file
    is announced to the browser server), and
  - a system-prompt fragment teaching the agent that backend's idioms.

The registry pattern mirrors ``embry0/agents/providers.py``. Design record:
``docs/spikes/2026-08-27-vibium-browser-backend.md``.

Two entries: ``playwright`` (the default, extracted verbatim from the
previously-duplicated QA seeds in #230) and ``vibium`` (#233, prompt
fragment + storage-state conversion in #234).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class BrowserBackend:
    """One selectable browser backend for the QA agent.

    Immutable by construction (frozen dataclass, tuples, mapping proxies);
    accessor methods hand out fresh mutable copies so agent definitions
    built from the registry never share state with it or with each other.
    """

    name: str
    # Key under which the server appears in an agent definition's
    # `mcp_servers` — also the `<server>` segment of `mcp__<server>__<tool>`.
    mcp_server_name: str
    mcp_command: str
    mcp_args: tuple[str, ...]
    # Fully-qualified `mcp__<server>__<tool>` names granted to the agent.
    tool_names: tuple[str, ...]
    # Backend-specific sandbox env, injected for every job on this backend.
    env: Mapping[str, str]
    # Env contract announcing a pre-seeded storageState file to the browser
    # server. Values may contain a `{path}` placeholder — format via
    # `storage_state_environ()`. Empty for backends whose restore mechanism
    # is a tool call rather than an env var.
    storage_state_env: Mapping[str, str]
    # Appended to the QA system prompt when this backend is selected.
    prompt_fragment: str

    def mcp_servers(self) -> dict[str, Any]:
        """Fresh `mcp_servers` dict for an agent definition."""
        return {
            self.mcp_server_name: {
                "type": "stdio",
                "command": self.mcp_command,
                "args": list(self.mcp_args),
            }
        }

    def browser_tools(self) -> list[str]:
        """Fresh list of the backend's fully-qualified MCP tool names."""
        return list(self.tool_names)

    def storage_state_environ(self, path: str) -> dict[str, str]:
        """The storage-state env vars for a state file at `path`."""
        return {key: value.format(path=path) for key, value in self.storage_state_env.items()}


# Advertised by @playwright/mcp but deliberately kept out of the QA agent's
# allowlist (the same reasoning as the vibium exclusions below):
# run_code_unsafe executes arbitrary Playwright code in the MCP process,
# and emulate_media changes the rendering context of the page under test.
PLAYWRIGHT_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "mcp__playwright__browser_run_code_unsafe",
        "mcp__playwright__browser_emulate_media",
    }
)

PLAYWRIGHT = BrowserBackend(
    name="playwright",
    mcp_server_name="playwright",
    # The GLOBAL playwright-mcp binary (installed via npm install -g in the
    # QA sandbox image), NOT `npx -y @playwright/mcp@latest`. The npx form
    # downloads a fresh copy each invocation whose bundled playwright-core
    # may not match the chromium pre-installed at build time, causing
    # "BrowserIsNotInstalled" at runtime. The global install is paired with
    # its bundled playwright-core's chromium in the Dockerfile.
    mcp_command="playwright-mcp",
    # --browser chromium because the Dockerfile pre-installs Chromium, not
    # Chrome stable (Playwright MCP's default), and the QA sandbox has no
    # system Chrome at /opt/google/chrome.
    mcp_args=("--headless", "--browser", "chromium"),
    # Every tool the image's @playwright/mcp advertises except
    # PLAYWRIGHT_EXCLUDED_TOOLS; CI runs the server from the built image and
    # checks that the two sets together are exactly its tools/list (#81).
    tool_names=(
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
    ),
    env=MappingProxyType({}),
    # playwright-mcp reads PLAYWRIGHT_MCP_STORAGE_STATE natively at
    # browser-context creation — but only for ISOLATED contexts (the default
    # persistent-profile mode uses launchPersistentContext, which cannot
    # accept storageState; the env var is silently ignored, verified against
    # @playwright/mcp 0.0.78 on job-ca502fac2ed4). Isolated mode keeps the
    # profile in memory, which is equivalent for QA: the persistent profile
    # was per-launch-temporary anyway.
    storage_state_env=MappingProxyType(
        {
            "PLAYWRIGHT_MCP_STORAGE_STATE": "{path}",
            "PLAYWRIGHT_MCP_ISOLATED": "1",
        }
    ),
    prompt_fragment="",
)


VIBIUM = BrowserBackend(
    name="vibium",
    mcp_server_name="vibium",
    # The vibium CLI (npm-installed, pinned in Dockerfile.sandbox.qa) serving
    # MCP over stdio. --headless: the sandbox has no display. --screenshot-dir
    # keeps captures under the per-subtask .qa tree (tmpfs-overlaid on shared
    # volumes) instead of vibium's ~/Pictures default.
    mcp_command="vibium",
    mcp_args=("mcp", "--headless", "--screenshot-dir", "/workspace/.qa/screenshots"),
    # The curated allowlist from docs/spikes/2026-08-27-vibium-browser-backend.md
    # (49 of the 85 served tools; verified against vibium 26.8.21 tools/list).
    # Deliberately excluded: raw mouse (bypasses actionability), clock mocking
    # (lets the agent fake time-dependent criteria), set_content (rewrites the
    # page under test), cookie read/write + storage export (session-state
    # exfiltration; auth flows through restore only), sleep/wait_for_fn, and
    # marginal read/emulation tools covered by the granted set.
    tool_names=(
        # Session / navigation
        "mcp__vibium__browser_start",
        "mcp__vibium__browser_stop",
        "mcp__vibium__browser_navigate",
        "mcp__vibium__browser_back",
        "mcp__vibium__browser_reload",
        "mcp__vibium__browser_get_url",
        "mcp__vibium__browser_get_title",
        # Interaction (auto-waiting)
        "mcp__vibium__browser_click",
        "mcp__vibium__browser_dblclick",
        "mcp__vibium__browser_type",
        "mcp__vibium__browser_fill",
        "mcp__vibium__browser_press",
        "mcp__vibium__browser_hover",
        "mcp__vibium__browser_check",
        "mcp__vibium__browser_uncheck",
        "mcp__vibium__browser_select",
        "mcp__vibium__browser_scroll",
        "mcp__vibium__browser_drag",
        "mcp__vibium__browser_upload",
        # Reading / locating
        "mcp__vibium__browser_find",
        "mcp__vibium__browser_find_all",
        "mcp__vibium__browser_get_text",
        "mcp__vibium__browser_get_attribute",
        "mcp__vibium__browser_is_visible",
        "mcp__vibium__browser_map",
        "mcp__vibium__browser_diff_map",
        "mcp__vibium__browser_a11y_tree",
        # Waiting
        "mcp__vibium__browser_wait",
        "mcp__vibium__browser_wait_for_url",
        "mcp__vibium__browser_wait_for_load",
        "mcp__vibium__browser_wait_for_text",
        # Evidence / diagnostics
        "mcp__vibium__browser_evaluate",
        "mcp__vibium__browser_screenshot",
        "mcp__vibium__browser_record_start",
        "mcp__vibium__browser_record_stop",
        "mcp__vibium__browser_record_start_group",
        "mcp__vibium__browser_record_stop_group",
        "mcp__vibium__browser_record_start_chunk",
        "mcp__vibium__browser_record_stop_chunk",
        # Tabs / frames
        "mcp__vibium__browser_new_page",
        "mcp__vibium__browser_list_pages",
        "mcp__vibium__browser_switch_page",
        "mcp__vibium__browser_frames",
        "mcp__vibium__browser_frame",
        # Viewport
        "mcp__vibium__browser_set_viewport",
        "mcp__vibium__browser_get_viewport",
        # Dialogs
        "mcp__vibium__browser_dialog_accept",
        "mcp__vibium__browser_dialog_dismiss",
        # State (auth pre-seeding — restore only, never export)
        "mcp__vibium__browser_restore_storage",
    ),
    # Chrome refuses its own sandbox when the container user is root (DinD
    # worst case) and vibium deliberately errors rather than auto-disabling
    # (upstream #141). Injected per job rather than baked as an image ENV so
    # non-QA uses of the image don't silently inherit a weakened browser.
    env=MappingProxyType({"VIBIUM_CHROME_ARGS": "--no-sandbox"}),
    # No env contract: vibium applies a pre-seeded storageState via the
    # browser_restore_storage TOOL CALL (prompt-driven restore-first, #234) —
    # the generic QA_STORAGE_STATE_PATH carries the file's location.
    storage_state_env=MappingProxyType({}),
    # Appended AFTER the QA system prompt: overrides its Playwright-specific
    # instructions for vibium runs (#234). Restore-first auth, evaluate-shim
    # console/network evidence, trace recording, map-first targeting.
    prompt_fragment="""

=== VIBIUM BROWSER BACKEND — overrides the Playwright specifics above ===
This run drives the browser through Vibium MCP, not Playwright MCP. Where
the instructions above name Playwright or its tools, the rules in this
section win.

Session lifecycle:
  - browser_start is your FIRST browser action. The engine is Chrome — never
    pass engine: firefox (not installed; it would error).
  - Immediately after browser_start, call browser_record_start (screenshots
    and snapshots on) so the whole session is captured as a trace.
  - Before writing result.json, call browser_record_stop with
    path=/workspace/.qa/traces/session.zip and upload it via the presign
    flow under "traces/session.zip". The ZIP opens in the Playwright trace
    viewer — it is primary failure evidence.

Pre-authenticated sessions (pre_authenticated: true) — NAVIGATE, RESTORE, RELOAD:
  Unlike Playwright, the seeded session is NOT applied automatically. The
  orchestrator wrote it (already converted to vibium's native restore
  shape) to the file named by the QA_STORAGE_STATE_PATH env var. Restoring
  web storage only works while the browser is ON the app's origin —
  verified against vibium 26.8.21: restoring any earlier throws a
  SecurityError or silently drops localStorage. Your first browser actions
  MUST be, in this exact order:
    1. browser_start
    2. browser_navigate to frontend_url (this first load may look logged
       out — that is expected, do not judge auth yet)
    3. browser_restore_storage with path = $QA_STORAGE_STATE_PATH
    4. browser_navigate to frontend_url AGAIN (reload so the app boots
       with the restored session)
  Judge authentication only AFTER step 4. If step 2 redirected you off the
  app's origin (an external IdP login page) and step 3 then errors, record
  the auth-dependent criteria status=inconclusive noting the redirect. If
  a login/consent screen persists AFTER step 4, the injected session is
  invalid or expired: screenshot it, mark the affected criteria
  status=inconclusive noting the session was rejected, and move on —
  never improvise credentials or drive a login form.

Element targeting (token-cheap, in preference order):
  - browser_map returns the interactive elements with @e1-style refs; pass
    a ref as the selector for click/fill/etc. browser_diff_map after an
    action shows what changed. browser_a11y_tree replaces browser_snapshot
    for full page structure.
  - browser_find accepts semantic locators (role/text/label/placeholder/
    testid) besides CSS.

Console & network evidence — Vibium MCP has NO console/network tools.
  Install this recorder via browser_evaluate IMMEDIATELY after EVERY
  navigation (a full page load wipes it — re-install each time):

    (() => { if (window.__qaRec) return "already installed";
      const r = window.__qaRec = {console: [], network: [], errors: []};
      for (const lvl of ["error", "warn"]) {
        const orig = console[lvl].bind(console);
        console[lvl] = (...a) => { r.console.push(lvl + ": " + a.map(String).join(" ")); orig(...a); };
      }
      window.addEventListener("error", e => r.errors.push(String(e.message)));
      window.addEventListener("unhandledrejection", e => r.errors.push("unhandledrejection: " + String(e.reason)));
      const of = window.fetch && window.fetch.bind(window);
      if (of) window.fetch = (...a) => of(...a).then(
        resp => { if (!resp.ok) r.network.push({url: resp.url, status: resp.status}); return resp; },
        err => { r.network.push({url: String(a[0]), status: 0}); throw err; });
      const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(m, u) { this.__qaUrl = String(u); return oo.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function() {
        this.addEventListener("loadend", () => {
          if (this.status >= 400 || this.status === 0) r.network.push({url: this.__qaUrl, status: this.status}); });
        return os.apply(this, arguments); };
      return "installed"; })()

  Read it back when verifying a criterion or diagnosing a failure:
    browser_evaluate: JSON.stringify(window.__qaRec)
  Its console/errors entries feed result.json's console_errors; its network
  entries feed network_failures. LIMITS (state them in notes when relevant):
  the recorder cannot see anything from before its installation (boot-time
  errors) or non-fetch/XHR traffic (websockets, workers, beacons).

Tool-name mapping for every rule above:
  browser_take_screenshot  -> browser_screenshot — ALWAYS pass a filename
                              (e.g. filename: "exploratory-criterion1.png");
                              the file lands in /workspace/.qa/screenshots
                              for upload via the presign flow. NEVER call it
                              without filename: that returns the whole image
                              as inline base64, wasting your context on a
                              megabyte of pixels. fullPage for page-level
                              evidence; annotate numbers the interactive
                              elements.
  browser_snapshot         -> browser_map (interactive) / browser_a11y_tree (structure)
  browser_console_messages -> the recorder shim (window.__qaRec.console/.errors)
  browser_network_requests -> the recorder shim (window.__qaRec.network)
  browser_wait_for         -> browser_wait / browser_wait_for_text / browser_wait_for_url
""",
)


DEFAULT_BROWSER_BACKEND = PLAYWRIGHT.name

_BACKENDS: dict[str, BrowserBackend] = {backend.name: backend for backend in (PLAYWRIGHT, VIBIUM)}


def backend_for(name: str) -> BrowserBackend:
    """Look up a browser backend by name. Raises ValueError on unknown names."""
    try:
        return _BACKENDS[name]
    except KeyError:
        raise ValueError(f"unknown browser backend {name!r}; known backends: {sorted(_BACKENDS)}") from None


def backend_names() -> tuple[str, ...]:
    """All registered backend names, sorted."""
    return tuple(sorted(_BACKENDS))


def to_vibium_storage_state(playwright_state: Mapping[str, Any], frontend_url: str) -> dict[str, Any]:
    """Convert a Playwright storageState to vibium 26.8.21's native restore shape.

    Established empirically against the pinned binary (#234): vibium's
    ``storage restore`` applies web storage ONLY from its own single-origin
    shape — ``{cookies, storage: {origin, localStorage: {k: v},
    sessionStorage: {k: v}}}``. Feeding it Playwright's ``origins[]`` form
    "succeeds" but silently restores cookies only, which breaks every SPA
    that keeps its session in localStorage. Cookies pass through untouched
    (the Playwright cookie fields restore fine).

    Native shape is single-origin: we keep the ``origins[]`` entry matching
    ``frontend_url``'s origin (fallback: the first entry). Anything else is
    dropped — the caller logs when that loses data.
    """
    from urllib.parse import urlparse

    parsed = urlparse(frontend_url)
    target_origin = f"{parsed.scheme}://{parsed.netloc}"
    origins = list(playwright_state.get("origins") or [])
    chosen = next((o for o in origins if o.get("origin") == target_origin), origins[0] if origins else {})
    local_storage = {
        entry["name"]: entry.get("value", "")
        for entry in chosen.get("localStorage") or []
        if isinstance(entry, dict) and "name" in entry
    }
    return {
        "cookies": list(playwright_state.get("cookies") or []),
        "storage": {
            "origin": chosen.get("origin") or target_origin,
            "localStorage": local_storage,
            # Playwright storageState carries no sessionStorage; present-but-
            # empty keeps the shape identical to vibium's own export.
            "sessionStorage": {},
        },
    }


# Prefixes identifying ANY registered backend's MCP tools — what
# with_browser_backend() strips before granting the selected backend's set.
_BROWSER_TOOL_PREFIXES: tuple[str, ...] = tuple(f"mcp__{b.mcp_server_name}__" for b in _BACKENDS.values())


def with_browser_backend(definition: Mapping[str, Any], backend: BrowserBackend) -> dict[str, Any]:
    """A merged copy of a QA agent definition wired to `backend`.

    Replaces the browser MCP server(s) and browser tool names; everything
    else — base tools (Read, Bash, …), NON-browser MCP servers/tools (e.g.
    an invocation-scoped context server), skills, model — survives
    unchanged. Appends the backend's prompt_fragment when non-empty. The
    stored seeds stay untouched (same pattern as generic_agent's
    _with_watcher_log_tool).

    For the backend the definition already carries this is byte-identical
    to the input (guaranteed by tests — the playwright no-op regression).
    """
    merged = dict(definition)

    kept_tools = [t for t in merged.get("tools") or [] if not t.startswith(_BROWSER_TOOL_PREFIXES)]
    merged["tools"] = [*kept_tools, *backend.browser_tools()]

    kept_servers = {
        name: dict(cfg)
        for name, cfg in (merged.get("mcp_servers") or {}).items()
        if name not in {b.mcp_server_name for b in _BACKENDS.values()}
    }
    merged["mcp_servers"] = {**kept_servers, **backend.mcp_servers()}

    if backend.prompt_fragment:
        merged["system_prompt"] = (merged.get("system_prompt") or "") + backend.prompt_fragment

    return merged

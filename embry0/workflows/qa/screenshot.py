"""Best-effort diagnostic screenshot for boot-timeout cases.

Backend-aware (#235): the playwright path runs a one-shot Node script via
the bundled playwright-core; the vibium path drives the vibium CLI. Both
are docker exec'd into the QA sandbox (Dockerfile.sandbox.qa bakes both
runtimes). Returns the PNG bytes for upload to MinIO, or None on any
failure (best-effort — the orchestrator must NEVER block on diagnostics).

Keeping the vibium path CLI-based (not playwright-core) is what allows a
future Playwright-free QA image (spike decision 7, revised).
"""

from __future__ import annotations

import json
import shlex
from typing import Any

import structlog

from embry0.execution import sandbox_files
from embry0.workflows.qa.browser_backends import DEFAULT_BROWSER_BACKEND, VIBIUM

#: A boot screenshot larger than this is refused rather than read out.
MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024

logger = structlog.get_logger(__name__)

# Path to the playwright-core CLI bundled with @playwright/mcp inside the QA sandbox.
# Matches Dockerfile.sandbox.qa's install layout.
_PLAYWRIGHT_CORE_DIR = "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright-core"


def _build_node_script(url: str, path: str) -> str:
    """Build a self-contained Node one-liner that captures a screenshot.

    Uses JSON-serialized URL/path so they're impossible to shell-quote-break.
    Resolves playwright-core via require.resolve to match the QA image layout
    (the bundled MCP playwright-core, NOT the orchestrator's standalone one).
    """
    url_lit = json.dumps(url)
    path_lit = json.dumps(path)
    return (
        f"const {{ chromium }} = require({json.dumps(_PLAYWRIGHT_CORE_DIR)});"
        "(async () => {"
        "  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });"
        "  try {"
        "    const page = await browser.newPage();"
        f"    await page.goto({url_lit}, {{ timeout: 15000, waitUntil: 'load' }}).catch(() => null);"
        f"    await page.screenshot({{ path: {path_lit}, fullPage: true }}).catch(() => null);"
        "  } finally {"
        "    await browser.close();"
        "  }"
        "})().catch((e) => { console.error(e); process.exit(2); });"
    )


def _build_vibium_script(url: str, path: str) -> str:
    """Bash one-liner driving the vibium CLI for the same capture.

    Mirrors the Node script's best-effort semantics: a failed navigation is
    tolerated (`|| true` — Chrome's error page IS the diagnostic for an
    unreachable frontend), the screenshot of whatever the browser shows is
    the artifact, the daemon is stopped so no Chrome lingers in the
    sandbox, and success is "a non-empty PNG exists".
    """
    url_q = shlex.quote(url)
    path_q = shlex.quote(path)
    return (
        f"vibium --headless go {url_q} >/dev/null 2>&1 || true; "
        f"vibium --headless screenshot -o {path_q} >/dev/null 2>&1; rc=$?; "
        "vibium daemon stop >/dev/null 2>&1 || true; "
        f"test -s {path_q} && exit 0; exit $rc"
    )


async def take_diagnostic_screenshot(
    *,
    docker: Any,
    container_id: str,
    frontend_url: str,
    sandbox_path: str = "/tmp/.embry0-boot-screenshot.png",
    browser_backend: str = DEFAULT_BROWSER_BACKEND,
) -> bytes | None:
    """Capture frontend_url via the job's browser backend; return PNG bytes.

    Returns None on any failure (network, browser crash, missing file).
    """
    try:
        if browser_backend == VIBIUM.name:
            cmd = ["bash", "-c", _build_vibium_script(frontend_url, sandbox_path)]
            # --no-sandbox for root-in-DinD
            exec_cmd = docker.build_exec_cmd(container_id, cmd, env=dict(VIBIUM.env))
        else:
            cmd = ["node", "-e", _build_node_script(frontend_url, sandbox_path)]
            exec_cmd = docker.build_exec_cmd(container_id, cmd)
        await docker.run_cmd(exec_cmd, timeout=30)
    except Exception as exc:
        logger.warning("diagnostic_screenshot_run_failed", backend=browser_backend, error=str(exc))
        return None

    try:
        result: bytes | None = await sandbox_files.read_regular_file(
            docker, container_id, sandbox_path, limit=MAX_SCREENSHOT_BYTES
        )
        return result
    except Exception as exc:
        logger.warning("diagnostic_screenshot_copy_failed", error=str(exc))
        return None

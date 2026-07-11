"""Best-effort diagnostic screenshot for boot-timeout cases.

Runs Playwright via a one-shot Node script docker exec'd into the QA sandbox
(which already has @playwright/mcp + chromium installed by Dockerfile.sandbox.qa).
Returns the PNG bytes for upload to MinIO, or None on any failure (best-effort —
the orchestrator must NEVER block on diagnostics).
"""

from __future__ import annotations

import json
from typing import Any

import structlog

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


async def take_diagnostic_screenshot(
    *,
    docker: Any,
    container_id: str,
    frontend_url: str,
    sandbox_path: str = "/tmp/.embry0-boot-screenshot.png",
) -> bytes | None:
    """Capture frontend_url via Playwright inside the sandbox; return PNG bytes.

    Returns None on any failure (network, Playwright crash, missing file).
    """
    script = _build_node_script(frontend_url, sandbox_path)
    cmd = ["node", "-e", script]
    try:
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, cmd),
            timeout=30,
        )
    except Exception as exc:
        logger.warning("diagnostic_screenshot_run_failed", error=str(exc))
        return None

    try:
        result: bytes | None = await docker.copy_bytes_from(container_id, sandbox_path)
        return result
    except Exception as exc:
        logger.warning("diagnostic_screenshot_copy_failed", error=str(exc))
        return None

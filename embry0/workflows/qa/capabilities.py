"""What a sandbox profile can do, read from its stored row.

One rule shared by the QA orchestrator's config-time capability check and
the free test-setup check (services/qa_config_check.py), so a setup the
check passes is one the orchestrator will not refuse for want of a browser.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def profile_has_browser(profile: Mapping[str, Any]) -> bool:
    """True when the profile's image bundles a browser.

    The QA agent always drives a browser (Playwright MCP is in its static
    seed). Only the qa image family bundles Playwright+Chromium, so browser
    capability is inferred from ``base_image``; custom profiles derived from
    that image pass automatically.
    """
    return "embry0-sandbox-qa" in str(profile.get("base_image") or "")

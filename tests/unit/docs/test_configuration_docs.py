"""Every setting in ``Embry0Config`` is named where an operator looks for it (#163).

An operator reads ``docs/configuration.md`` and ``.env.example``. A setting
named in neither exists only for whoever reads ``embry0/config.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from embry0.config import Embry0Config

ROOT = Path(__file__).resolve().parents[3]

#: Older settings documented nowhere yet. Shrink-only: document one and delete
#: it here (the second test says when). Never add a new setting to this list.
ALLOWED_UNDOCUMENTED = frozenset(
    {
        "COMMENT_DISPATCH_AUTHOR_ROLES",
        "COMMENT_DISPATCH_ENABLED",
        "COMMENT_DISPATCH_TAG",
        "DAILY_DIGEST_ENABLED",
        "DAILY_DIGEST_HOUR_UTC",
        "HTTP_CONTEXT_ALLOW_PRIVATE",
        "HTTP_CONTEXT_MAX_BYTES",
        "HTTP_CONTEXT_TIMEOUT_SECONDS",
        "LOCAL_CONTEXT_ALLOWLIST",
        "LOCAL_CONTEXT_MAX_BYTES",
        "LOCAL_CONTEXT_MAX_FILES",
        "PROXY_ENROLL_TTL_SECONDS",
        "PR_DEDUP_ENABLED",
        "PR_DEDUP_MAX_PRS",
        "QA_BROWSER_BACKEND_DEFAULT",
        "WATCHER_JOB_TIMEOUT_SECONDS",
        "WATCHER_LINEAR_PROJECT_ID",
        "WATCHER_MAX_DEFERRED_FINDINGS",
        "WATCHER_MAX_FULL_CAP_ANALYSES_PER_DAY",
        "WATCHER_MAX_OPEN_PROPOSALS",
        "WATCHER_PROPOSAL_WINDOW_DAYS",
        "WEBHOOK_RATE_LIMIT_PER_MINUTE",
    }
)


def _setting_names() -> set[str]:
    return {(field.alias or name).upper() for name, field in Embry0Config.model_fields.items()}


def _documented(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Z0-9_]){re.escape(name)}(?![A-Z0-9_])", text) is not None


def _docs_text() -> str:
    return (ROOT / "docs" / "configuration.md").read_text() + (ROOT / ".env.example").read_text()


def test_every_setting_is_named_in_the_configuration_docs_or_the_example_env() -> None:
    text = _docs_text()
    missing = sorted(n for n in _setting_names() if n not in ALLOWED_UNDOCUMENTED and not _documented(text, n))
    assert missing == [], (
        f"settings named in neither docs/configuration.md nor .env.example: {missing}; "
        "document each where an operator will look"
    )


def test_the_allowance_only_shrinks() -> None:
    text = _docs_text()
    names = _setting_names()
    stale = sorted(n for n in ALLOWED_UNDOCUMENTED if n not in names or _documented(text, n))
    assert stale == [], f"documented now, or no longer a setting; delete from ALLOWED_UNDOCUMENTED: {stale}"

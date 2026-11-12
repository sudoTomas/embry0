"""What a failed resolution says wherever a job's error is read: the five messages, exactly.

A job that fails because no credential resolves, or because a pinned one cannot
be used, stores the resolver's message as its error, its event and the comment
posted about it, for whoever reads the job. So the message names no credential
but one pinned to the repository itself, carries no step of the resolution,
and ends with a fixed pointer to the dry run, which shows each reader the
steps it may read. The stored rows a migration rewrites take these texts.
"""

from __future__ import annotations

from collections.abc import Awaitable

import pytest

from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError
from embry0.services.credentials.shown import error_shown
from tests.unit.services.credentials.test_resolve_llm import _llm
from tests.unit.services.credentials.test_resolve_llm import _row as _llm_row
from tests.unit.services.credentials.test_resolver import _resolver
from tests.unit.services.credentials.test_resolver import _row as _forge_row

REPO = "acme/widgets"
FORGE_WHY = f"; GET /credentials/resolve?repo={REPO} shows why"
LLM_WHY = f"; GET /credentials/resolve?repo={REPO}&role=llm&provider=anthropic shows why"


async def _failure(resolve: Awaitable[object]) -> CredentialMissingError | CredentialUnavailableError:
    try:
        await resolve
    except (CredentialMissingError, CredentialUnavailableError) as exc:
        return exc
    raise AssertionError("the resolution did not fail")


async def test_each_message_names_no_step_and_ends_with_where_to_read_why() -> None:
    disabled = {"status": "disabled", "pinned": True}
    failures = {
        "forge missing": await _failure(_resolver().resolve_forge(REPO, "github")),
        "model account missing": await _failure(_llm().resolve_llm(REPO, "anthropic")),
        "pinned at the repository": await _failure(
            _llm({("repo", REPO, "llm", "anthropic"): [_llm_row("ours", **disabled)]}).resolve_llm(REPO, "anthropic")
        ),
        "pinned at the owner": await _failure(
            _llm({("owner", "github:acme", "llm", "anthropic"): [_llm_row("theirs", **disabled)]}).resolve_llm(
                REPO, "anthropic"
            )
        ),
        "pinned at the deployment": await _failure(
            _resolver({("host", "github.com", "forge", "github"): [_forge_row("shared", **disabled)]}).resolve_forge(
                REPO, "github"
            )
        ),
    }
    assert {name: str(exc) for name, exc in failures.items()} == {
        "forge missing": f"no github credential for '{REPO}': bind one in the registry or set GITHUB_TOKEN{FORGE_WHY}",
        "model account missing": f"no anthropic credential for '{REPO}': bind one in the registry{LLM_WHY}",
        "pinned at the repository": f"credential 'ours' is pinned for {REPO} but disabled{LLM_WHY}",
        "pinned at the owner": f"the owner's credential pinned for {REPO} cannot be used{LLM_WHY}",
        "pinned at the deployment": f"the deployment's credential pinned for {REPO} cannot be used{FORGE_WHY}",
    }


@pytest.mark.parametrize("provider", [None, "anthropic"])
async def test_the_dry_run_answers_without_the_pointer_to_itself(provider: str | None) -> None:
    """``GET /credentials/resolve`` builds its answer from the error: the message without the pointer, then the
    steps, as before the pointer existed, for a caller that reads every scope."""
    failure = await _failure(
        _resolver().resolve_forge(REPO, "github") if provider is None else _llm().resolve_llm(REPO, provider)
    )
    shown = error_shown(failure, None)
    assert "shows why" not in shown and shown.endswith(f"(trace: {'; '.join(failure.trace)})"), shown

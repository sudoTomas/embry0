"""Repos that were renamed on the forge, and how to normalise URLs that still
name the old slug (#277).

GitHub keeps redirecting an old repo URL, so a stale stored URL still *opens*
— but it no longer *equals* the URL the forge reports for the same PR or
issue, and URL equality is what merge sync and dedup key on. Nothing rewrites
stored rows after a rename: :func:`normalize_repo_url` brings a URL arriving
at runtime (a webhook payload, an API response that predates the rename) to
the current slug, and :func:`repo_url_variants` / :func:`repo_slug_variants`
look a stored row up under every spelling it may carry.

Which repos a deployment renamed is that deployment's configuration, not the
product's (#527): the map is ``REPO_RENAMES`` in the environment, installed
once per process by :func:`configure_repo_renames`. Unset, nothing is renamed.

A slug is only matched as the ``owner/repo`` segment of a GitHub repo URL and
only up to a path boundary, so ``widgets-frontend`` or a Linear URL that
merely mentions the old name is left alone. Renames are single-hop: chain a
twice-renamed repo by mapping BOTH old slugs to the current one.
"""

import json
import re
from collections.abc import Mapping
from typing import Final

import structlog

logger = structlog.get_logger(__name__)

# old owner/repo -> current owner/repo: the renames this process applies.
# Keys are matched case-insensitively (GitHub slugs are); values are written
# exactly as given. Empty until configure_repo_renames() installs the
# operator's REPO_RENAMES; the product ships none.
REPO_RENAMES: Mapping[str, str] = {}

_URL_PREFIX: Final = r"^(https?://(?:www\.)?github\.com/)"
_PATH_BOUNDARY: Final = r"($|[/?#])"
# What a slug may contain. It is interpolated into a regex replacement, so a
# backslash or a newline must never get through.
_SLUG_RE: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _pattern(old_slug: str) -> str:
    return f"{_URL_PREFIX}{re.escape(old_slug)}{_PATH_BOUNDARY}"


def _mapping(renames: Mapping[str, str] | None) -> Mapping[str, str]:
    # Resolved at call time. A default argument would capture REPO_RENAMES
    # at import, and the map installed at startup (or a test's stand-in)
    # would never be seen by merge sync or the healing ledger.
    return REPO_RENAMES if renames is None else renames


def parse_repo_renames(raw: str) -> tuple[dict[str, str], list[str]]:
    """``REPO_RENAMES`` as a mapping, plus why each refused part was refused.

    The value is a JSON object, ``{"old-owner/old-name": "owner/name"}``;
    empty or blank means no renames. A value that is not a JSON object maps
    nothing. An entry is dropped when either side is not an ``owner/repo``
    slug, or when the slug it maps to is renamed too: a chain would stop
    :func:`normalize_repo_url` being idempotent. Never raises, because a typo
    here must not stop startup; the caller reports the problems.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return {}, []
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return {}, [f"not valid JSON: {exc}"]
    if not isinstance(parsed, dict):
        return {}, [f"must be a JSON object of old slug to current slug, not {type(parsed).__name__}"]

    renames: dict[str, str] = {}
    problems: list[str] = []
    for old, new in parsed.items():
        if not (isinstance(new, str) and _SLUG_RE.fullmatch(old) and _SLUG_RE.fullmatch(new)):
            problems.append(f"{old!r} -> {new!r}: both sides must be an owner/repo slug")
            continue
        renames[old] = new
    renamed = {old.lower() for old in renames}
    for old, new in list(renames.items()):
        if new.lower() != old.lower() and new.lower() in renamed:
            problems.append(f"{old!r} -> {new!r}: {new!r} is renamed too; map {old!r} to the current slug")
            del renames[old]
    return renames, problems


def configure_repo_renames(raw: str) -> Mapping[str, str]:
    """Install ``REPO_RENAMES`` for this process and return what it installed.

    Called once per process before merge sync or the healing ledger runs: by
    the orchestrator's startup and by ``embry0 merge-sync``. Each refused
    part is logged as an error; the rest of the map still applies.
    """
    global REPO_RENAMES
    renames, problems = parse_repo_renames(raw)
    for problem in problems:
        logger.error(
            "repo_renames_invalid",
            problem=problem,
            hint='REPO_RENAMES is a JSON object such as {"acme/old-name": "acme/new-name"}',
        )
    REPO_RENAMES = renames
    if renames:
        logger.info("repo_renames_configured", renames=renames)
    return renames


def normalize_repo_slug(slug: str, renames: Mapping[str, str] | None = None) -> str:
    """``owner/repo`` with a known rename applied; anything else unchanged."""
    lowered = slug.lower()
    for old, new in _mapping(renames).items():
        if old.lower() == lowered:
            return new
    return slug


def same_repository(a: str | None, b: str | None, renames: Mapping[str, str] | None = None) -> bool:
    """Whether ``a`` and ``b`` name one repository: equal ignoring case once a known rename is applied to each.
    False when either is empty."""
    if not a or not b:
        return False
    mapping = _mapping(renames)
    return normalize_repo_slug(a, mapping).lower() == normalize_repo_slug(b, mapping).lower()


def normalize_repo_url[T: (str, None)](url: T, renames: Mapping[str, str] | None = None) -> T:
    """``url`` with a renamed repo's old slug replaced by its current one.

    Idempotent; ``None``/empty and any URL that is not a repo URL of a renamed
    repo pass through untouched. Omit ``renames`` to use :data:`REPO_RENAMES`
    as it stands at the call, not as it stood at import.
    """
    if not url:
        return url
    for old, new in _mapping(renames).items():
        rewritten, count = re.subn(_pattern(old), rf"\g<1>{new}\g<2>", url, count=1, flags=re.IGNORECASE)
        if count:
            return rewritten
    return url


def repo_slug_variants(slug: str, renames: Mapping[str, str] | None = None) -> list[str]:
    """Every slug a stored row may carry for this repo: the current one first,
    then each old name that maps to it. For equality lookups over columns
    written before the rename."""
    mapping = _mapping(renames)
    current = normalize_repo_slug(slug, mapping)
    return [current, *(old for old, new in mapping.items() if new.lower() == current.lower())]


def repo_url_variants(url: str, renames: Mapping[str, str] | None = None) -> list[str]:
    """``url`` normalised, plus the same URL under each old slug of its repo —
    the URL rendering of :func:`repo_slug_variants`."""
    mapping = _mapping(renames)
    current = normalize_repo_url(url, mapping)
    variants = [current]
    for old, new in mapping.items():
        rewritten, count = re.subn(_pattern(new), rf"\g<1>{old}\g<2>", current, count=1, flags=re.IGNORECASE)
        if count:
            variants.append(rewritten)
    return variants

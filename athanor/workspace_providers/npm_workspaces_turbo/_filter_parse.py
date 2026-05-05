"""Parse `NpmTurboConfig.affected_filter` for the `${base_branch}` token.

Phase 3 honors only the base-branch interpolation. Full turbo filter syntax
(refs, range syntax `A...B`, tag filters) is treated as advisory and logged
at INFO. The parser never raises; malformed input falls back to the default
base branch with a warning string.
"""

from __future__ import annotations

from dataclasses import dataclass

_BASE_BRANCH_TOKEN = "${base_branch}"


@dataclass(frozen=True, slots=True)
class FilterParseResult:
    """Outcome of parsing `affected_filter`.

    `base_branch` is what the orchestrator should use for the `git diff`
    base ref. `has_base_branch_token` indicates whether the interpolation
    was found in the filter (False → fell back to default_base_branch).
    `warning` is set when the filter was provided but the parser couldn't
    use it (caller should log at WARNING).
    """

    base_branch: str
    has_base_branch_token: bool
    raw_filter: str | None
    warning: str | None = None


def parse_affected_filter(
    filter_text: str | None,
    *,
    default_base_branch: str,
) -> FilterParseResult:
    """Extract base_branch from a turbo affected_filter string.

    Recognizes only `${base_branch}` substitution. Everything else in the
    filter is opaque and ignored (advisory).
    """
    if not filter_text:
        return FilterParseResult(
            base_branch=default_base_branch,
            has_base_branch_token=False,
            raw_filter=filter_text,
            warning=None,
        )

    if _BASE_BRANCH_TOKEN in filter_text:
        return FilterParseResult(
            base_branch=default_base_branch,
            has_base_branch_token=True,
            raw_filter=filter_text,
            warning=None,
        )

    return FilterParseResult(
        base_branch=default_base_branch,
        has_base_branch_token=False,
        raw_filter=filter_text,
        warning=(
            f"affected_filter {filter_text!r} contains no ${{base_branch}} "
            f"interpolation; falling back to default_base_branch="
            f"{default_base_branch!r}"
        ),
    )

"""Tests for the affected_filter parser."""

from __future__ import annotations

from embry0.workspace_providers.npm_workspaces_turbo._filter_parse import (
    FilterParseResult,
    parse_affected_filter,
)


def test_default_filter_extracts_base_branch_token():
    """The default filter is `[origin/${base_branch}]` — parser identifies the
    interpolation, leaves remainder as opaque advisory."""
    r = parse_affected_filter("[origin/${base_branch}]", default_base_branch="main")
    assert isinstance(r, FilterParseResult)
    assert r.base_branch == "main"
    assert r.has_base_branch_token is True
    # The literal filter is preserved for INFO-level logging only.
    assert r.raw_filter == "[origin/${base_branch}]"


def test_explicit_branch_in_filter_falls_back_to_default():
    """A literal `[main]` (no ${base_branch} token) means the user hard-coded a
    ref; parser falls back to the default base_branch and warns."""
    r = parse_affected_filter("[main]", default_base_branch="develop")
    assert r.base_branch == "develop"
    assert r.has_base_branch_token is False
    assert r.warning is not None  # something like "no ${base_branch} interpolation found"


def test_empty_or_none_filter_uses_default():
    r = parse_affected_filter(None, default_base_branch="main")
    assert r.base_branch == "main"
    assert r.has_base_branch_token is False

    r2 = parse_affected_filter("", default_base_branch="main")
    assert r2.base_branch == "main"
    assert r2.has_base_branch_token is False


def test_default_base_branch_parameter_substitutes():
    """The interpolation always resolves to the default_base_branch arg —
    the parser doesn't try to detect the active branch on its own."""
    r = parse_affected_filter("[origin/${base_branch}]", default_base_branch="release-2026")
    assert r.base_branch == "release-2026"


def test_only_base_branch_interpolation_is_recognized():
    """Other ${...} tokens are not supported — Phase 3 honors only ${base_branch}."""
    r = parse_affected_filter("[origin/${head_sha}]", default_base_branch="main")
    assert r.base_branch == "main"  # falls back
    assert r.has_base_branch_token is False
    assert r.warning is not None

"""Container allowlist entries: exact names and glob patterns (RAV-3096)."""

from __future__ import annotations

import pytest

from embry0.execution.proxy.container_allowlist import ContainerAllowlist, is_pattern

# A stack whose API runs blue/green (shop-api-blue, shop-api-b-green, ...) next to
# fixed-name services.
_ENTRIES = ["shop-api*", "Shop-Web", "shop-ml"]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [("shop-api*", True), ("shop-api-?", True), ("shop-api-[bg]*", True), ("shop-api", False), ("", False)],
)
def test_is_pattern(entry: str, expected: bool) -> None:
    assert is_pattern(entry) is expected


def test_blank_entries_are_dropped_and_spelling_kept() -> None:
    allowlist = ContainerAllowlist([" shop-api* ", "", "  ", "Shop-Web"])
    assert allowlist.entries == ["shop-api*", "Shop-Web"]
    assert allowlist and allowlist.has_patterns
    assert not ContainerAllowlist(["", " "])
    assert not ContainerAllowlist(["shop-web"]).has_patterns


def test_exact_entries_behave_as_before() -> None:
    allowlist = ContainerAllowlist(_ENTRIES)
    # Case-insensitive, answered in the entry's own spelling.
    assert allowlist.resolve("shop-web") == "Shop-Web"
    assert allowlist.resolve(" SHOP-ML ") == "shop-ml"
    # An exact entry admits nothing longer: no implicit prefix match.
    assert allowlist.resolve("shop-ml-old") is None
    assert allowlist.resolve("shop-web-2") is None
    assert allowlist.resolve("") is None
    assert allowlist.resolve("   ") is None


@pytest.mark.parametrize(
    "name",
    ["shop-api-blue", "shop-api-b-green", "shop-api-leads-blue", "shop-api", "shop-api_2", "SHOP-API-BLUE"],
)
def test_a_pattern_admits_plain_names_as_given(name: str) -> None:
    assert ContainerAllowlist(_ENTRIES).resolve(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "shop-api.*",  # regex wildcard
        "shop-api|.+",  # alternation: every container
        "shop-api-blue|embry0-postgres",
        "shop-api*",  # the pattern itself is not a name
        "shop-api-(blue)",
        "shop-api blue",
        "shop-api-bl\nue",
        "shop-api-blue.old",
        "-shop-api",
        "shop-api-" + "x" * 200,  # over the log API's 200-character cap
    ],
)
def test_a_pattern_admits_nothing_regex_could_read_as_syntax(name: str) -> None:
    assert ContainerAllowlist(_ENTRIES).resolve(name) is None


def test_question_mark_and_class_patterns() -> None:
    allowlist = ContainerAllowlist(["shop-api-[bg]?", "shop-ocr-?"])
    assert allowlist.resolve("shop-api-b1") == "shop-api-b1"
    assert allowlist.resolve("shop-api-x1") is None
    assert allowlist.resolve("shop-ocr-2") == "shop-ocr-2"
    assert allowlist.resolve("shop-ocr-22") is None


def test_names_in_finds_whole_names_for_both_kinds() -> None:
    text = (
        "shop-api-leads-green logged 4x, then SHOP-WEB and shop-ml-old; "
        "see http://shop-api-b-green:3101/x. Not xshop-api-blue, not embry0-postgres."
    )
    assert ContainerAllowlist(_ENTRIES).names_in(text) == ["Shop-Web", "shop-api-b-green", "shop-api-leads-green"]


def test_names_in_without_patterns_is_the_exact_whole_word_match() -> None:
    text = "shop-web and SHOP-ML failed; not shop-web-old, not embry0-postgres, xshop-ml"
    assert ContainerAllowlist(["shop-web", "shop-ml", "shop-api"]).names_in(text) == ["shop-ml", "shop-web"]
    assert ContainerAllowlist([]).names_in(text) == []
    assert ContainerAllowlist(_ENTRIES).names_in("") == []

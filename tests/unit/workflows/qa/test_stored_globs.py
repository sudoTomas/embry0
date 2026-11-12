"""A stored glob this grammar refuses is read as the release before read it (#458's second review).

The release before translated a glob to a regex, and a class ending in a
backslash (``[\\]``) left the regex's class open, so it took in whatever
followed. This grammar refuses such a glob when it is written; one stored
before must keep reading after the upgrade, with the meaning it had. The
release before's translator is copied here, as it was on main, as the
reference.
"""

from __future__ import annotations

import random
import re
import warnings

import pytest
from pydantic import ValidationError

from embry0.workflows.qa._glob_match import GlobPatternError, _regex_before, compile_glob, read_glob
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

# ---- main's translator, verbatim but for its names ----------------------------------------------------------


def _main_segment(segment: str, *, pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            if j < len(segment) and segment[j] in "!^":
                j += 1
            if j < len(segment) and segment[j] == "]":
                j += 1  # a leading ']' is a literal member of the class
            while j < len(segment) and segment[j] != "]":
                j += 1
            if j >= len(segment):
                raise GlobPatternError(f"unterminated character class in glob {pattern!r}")
            cls = segment[i + 1 : j]
            if "/" in cls:
                raise GlobPatternError(f"character class may not contain '/' in glob {pattern!r}")
            if cls.startswith("!"):
                cls = "^" + cls[1:]
            out.append("[" + cls + "]")
            i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _main_compile(pattern: str) -> re.Pattern[str]:
    """Compile one glob into an anchored regex. Raises GlobPatternError."""
    if not isinstance(pattern, str) or not pattern:
        raise GlobPatternError("glob pattern must be a non-empty string")
    if pattern.startswith("/"):
        raise GlobPatternError(f"glob must be repo-relative (no leading '/'): {pattern!r}")
    segments = pattern.split("/")
    if any(s == "" for s in segments):
        raise GlobPatternError(f"empty path segment in glob {pattern!r}")
    if any(s == ".." for s in segments):
        raise GlobPatternError(f"'..' segments are not allowed in glob {pattern!r}")

    collapsed: list[str] = []
    for seg in segments:
        if "**" in seg and seg != "**":
            raise GlobPatternError(f"'**' must be its own path segment in glob {pattern!r}")
        if seg == "**" and collapsed and collapsed[-1] == "**":
            continue  # a/**/**/b ≡ a/**/b
        collapsed.append(seg)

    parts: list[str] = []
    for idx, seg in enumerate(collapsed):
        is_last = idx == len(collapsed) - 1
        if seg == "**":
            # Mid-pattern: zero or more whole segments (each with its '/').
            # Trailing: at least one character, i.e. ≥1 segment under the prefix.
            parts.append(".+" if is_last else "(?:[^/]+/)*")
        else:
            parts.append(_main_segment(seg, pattern=pattern) + ("" if is_last else "/"))
    return re.compile(r"\A" + "".join(parts) + r"\Z")


# ---------------------------------------------------------------------------------------------------------------

STORED_GLOB = "docs/[\\]*"


def _main(pattern: str) -> re.Pattern[str] | None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        try:
            return _main_compile(pattern)
        except (GlobPatternError, re.error):
            return None


def test_a_class_ending_in_a_backslash_is_refused_when_written_and_says_how_to_write_one() -> None:
    with pytest.raises(GlobPatternError, match="ends in a backslash.*written"):
        compile_glob(STORED_GLOB)
    assert compile_glob("docs/[\\\\]*").matches("docs/\\x"), "a doubled backslash is a backslash in a class"


def test_a_stored_glob_main_read_is_read_with_main_s_meaning() -> None:
    """Random globs this grammar refuses, most holding a class that ends in a backslash: wherever main compiled
    one, it reads, and matches every path main's regex matched, and no other; where main refused one, so does
    reading it."""
    rng = random.Random(4582)
    pieces = ["[\\]", "[!\\]", "*", "?", "a", "]", "[a]", "[!a]", "**", "b?", "-"]
    names = ["a", "]", "\\", "a]b", "^", "[", "", "x/y", "a\\b"]
    read = 0
    for _ in range(4000):
        pattern = "/".join(
            "".join(rng.choice(pieces) for _ in range(rng.randint(1, 3))) for _ in range(rng.randint(1, 3))
        )
        try:
            compile_glob(pattern)
            continue  # one this grammar writes: test_glob_match compares those with main's meaning
        except GlobPatternError:
            pass
        main = _main(pattern)
        if main is None:
            with pytest.raises(GlobPatternError):
                read_glob(pattern)
            continue
        glob = read_glob(pattern)
        read += 1
        for _ in range(10):
            path = "/".join(rng.choice(names) for _ in range(rng.randint(1, 4)))
            assert glob.matches(path) is bool(main.match(path)), f"{pattern!r} vs {path!r}: not main's reading"
    assert read > 500, f"only {read} globs main compiled were compared"


def test_the_regex_kept_for_stored_globs_is_main_s_own() -> None:
    rng = random.Random(4583)
    alphabet = ["[\\]", "[", "]", "*", "?", "**", "a", ".", "!", "^", "/", "-"]
    for _ in range(3000):
        pattern = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8)))
        main = _main(pattern)
        mine = _regex_before(pattern)
        assert (mine.pattern if mine else None) == (main.pattern if main else None), pattern


# ---- a test setup and a verification config holding one, written ------------------------------------------------

SETUP = """version: 2
qa_required: never
workspace_provider:
  type: npm-workspaces-turbo
conditional_acceptance_criteria:
  - name: docs
    when:
      changed_paths: ['docs/[\\]*']
    criteria:
      - "Open the docs site"
"""


def test_a_setup_holding_one_is_refused_when_written_saying_why() -> None:
    with pytest.raises(ValueError, match="ends in a backslash"):
        parse_qa_yaml_v2(SETUP, writing=True)


VERIFICATION = {"test_targets": [{"name": "docs", "paths": ["docs/[\\]*"], "command": "true"}]}


def test_a_verification_config_holding_one_is_refused_when_written_and_put_back_by_an_undo() -> None:
    from embry0.api.schemas.repo_preferences import RepoPreferencesUpdateRequest
    from embry0.workflows.verification.config import STORED

    with pytest.raises(ValidationError, match="ends in a backslash"):
        RepoPreferencesUpdateRequest.model_validate({"verification": VERIFICATION})
    RepoPreferencesUpdateRequest.model_validate({"verification": VERIFICATION}, context=STORED)

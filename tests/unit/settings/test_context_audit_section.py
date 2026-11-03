"""The settings document's ``context_audit`` section (#170): which files the context audit reads, how often it looks.

The files are a repository's own conventions, so an organization's layer and
a repository's may name their own; how often the schedule looks and how many
repositories it reads at once are the deployment's alone. Each test reaches
the section through the catalog, so a tree without it fails each one with
its own message.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from embry0.config import Embry0Config
from embry0.services.instruction_files import INSTRUCTION_FILE_GLOBS, SKIPPED_DIRS
from embry0.settings.bootstrap import env_values, import_env_once
from embry0.settings.env import governed_variables
from embry0.settings.registry import Section, SectionRefused
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore, Snapshot, build_snapshot
from embry0.storage.database import DatabasePool

WIDGETS = "acme/widgets"
GIZMOS = "acme/gizmos"
GADGETS = "globex/gadgets"


def _section() -> Section[Any]:
    found = CATALOG.get("context_audit")
    if found is None:
        pytest.fail("the settings document has no context_audit section: the audit's files and cadence are code")
    return found


def _rules(settings: Any) -> Any:
    """The files ``settings`` select, a ``FileRules``."""
    try:
        from embry0.settings.sections.context_audit import file_rules
    except ImportError:
        pytest.fail("embry0.settings.sections.context_audit has no file_rules: nothing turns the section into files")
    return file_rules(settings)


def _built_in() -> Any:
    """The built-in rules: what a repository reads while no document names its own files."""
    try:
        from embry0.services.instruction_files import DEFAULT_RULES
    except ImportError:
        pytest.fail("embry0.services.instruction_files has no DEFAULT_RULES: the files are one fixed list")
    return DEFAULT_RULES


def _snapshot(documents: Mapping[str, Mapping[str, Any]]) -> Snapshot:
    """A snapshot of ``documents``, each stored scope to its ``context_audit`` values, as the store builds one."""
    rows = [
        {"scope": scope, "data": {"context_audit": dict(values)}, "generation": 1, "updated_at": None}
        for scope, values in documents.items()
    ]
    if "deployment" not in documents:
        rows.append({"scope": "deployment", "data": {}, "generation": 1, "updated_at": None})
    return build_snapshot(CATALOG, rows, token=(), log=False)


def _refusal(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    with pytest.raises(SectionRefused) as refused:
        _section().validate(raw)
    return refused.value.errors


# ---- what nothing written reads ------------------------------------------------------


def test_nothing_written_reads_the_built_in_files_every_15_minutes_8_repositories_at_a_time() -> None:
    defaults = _section().defaults()
    assert defaults.model_dump(mode="json") == {
        "days": None,
        "open_prs": False,
        "files": None,
        "exclude_dirs": None,
        "check_minutes": 15,
        "read_concurrency": 8,
        "rate_reserve_percent": 20,
    }, "a deployment that writes nothing must read what the constants held: 900 s, 8 at once, the built-in lists"
    rules = _rules(defaults)
    assert rules == _built_in(), "null must read the built-in rules"
    assert (rules.globs, rules.skipped_dirs) == (INSTRUCTION_FILE_GLOBS, SKIPPED_DIRS)


def test_every_built_in_glob_and_directory_is_one_the_section_accepts() -> None:
    # An operator copies the built-in list to extend it; a copy the section refused could not be written.
    written = {"files": list(INSTRUCTION_FILE_GLOBS), "exclude_dirs": sorted(SKIPPED_DIRS)}
    model = _section().validate(written)
    assert _rules(model) == _built_in(), "the built-in lists written out read exactly as null does"


def test_a_list_replaces_the_built_in_one_whole_and_each_key_on_its_own() -> None:
    model = _section().validate({"files": ["**/CONVENTIONS.md", ".roo/rules/**/*.md"]})
    rules = _rules(model)
    assert rules.globs == ("**/CONVENTIONS.md", ".roo/rules/**/*.md"), "a written list is read in place of the built-in"
    assert rules.skipped_dirs == SKIPPED_DIRS, "exclude_dirs left null keeps the built-in directories"
    assert rules.select(["CLAUDE.md", "a/CONVENTIONS.md", ".roo/rules/x/style.md", "vendor/CONVENTIONS.md"]) == [
        ".roo/rules/x/style.md",
        "a/CONVENTIONS.md",
    ]
    nothing_skipped = _rules(_section().validate({"exclude_dirs": []}))
    assert nothing_skipped.globs == INSTRUCTION_FILE_GLOBS and nothing_skipped.skipped_dirs == frozenset()
    assert nothing_skipped.select(["vendor/lib/AGENTS.md"]) == ["vendor/lib/AGENTS.md"], "[] searches every directory"


# ---- what a glob may be ---------------------------------------------------------------


@pytest.mark.parametrize(
    "glob",
    [
        "CLAUDE.md",
        "*.md",
        "**/CONVENTIONS.md",
        ".roo/rules/**/*.md",
        ".kiro/steering/*.md",
        "docs/agents/*.md",
        "a/**/b/**/c-d_e+f@g.md",
    ],
)
def test_a_glob_both_readers_read_alike_is_accepted(glob: str) -> None:
    assert _section().validate({"files": [glob]}).files == (glob,)


@pytest.mark.parametrize(
    ("glob", "says"),
    [
        ("", "write a glob"),
        ("/CLAUDE.md", "no leading, trailing or doubled '/'"),
        ("docs/", "no leading, trailing or doubled '/'"),
        ("docs//CLAUDE.md", "no leading, trailing or doubled '/'"),
        ("../CLAUDE.md", "no '.' or '..' directory"),
        ("./CLAUDE.md", "no '.' or '..' directory"),
        ("rules/**", "'**' is never last"),
        ("a**/CLAUDE.md", "'**' stands alone between slashes"),
        ("***/CLAUDE.md", "'**' stands alone between slashes"),
        ("**/**/CLAUDE.md", "once in a row"),
        ("CLAUDE?.md", "only letters, digits"),
        ("[Cc]LAUDE.md", "only letters, digits"),
        ("{a,b}.md", "only letters, digits"),
        ("it's.md", "only letters, digits"),
        ("my notes.md", "only letters, digits"),
        ("a\\b.md", "only letters, digits"),
        ("x" * 201, "at most 200 characters"),
    ],
)
def test_a_glob_git_and_the_matcher_would_read_apart_is_refused_naming_the_key(glob: str, says: str) -> None:
    errors = _refusal({"files": ["**/CLAUDE.md", glob]})
    assert [str(e["loc"][0]) for e in errors] == ["files"], f"the refusal names the key: {errors}"
    message = " ".join(str(e["msg"]) for e in errors)
    assert says in message, f"{glob!r}: {message}"
    if len(glob) > 3:
        assert glob not in message, "a refusal never repeats the value"
    if len(glob) <= 200:
        assert "glob 2" in message, f"the refusal names which glob: {message}"


def test_a_list_names_one_glob_or_more_each_once_at_most_64() -> None:
    assert "at least one glob" in str(_refusal({"files": []})), "[] would list every file: null is the built-in list"
    assert "each glob once" in str(_refusal({"files": ["**/CLAUDE.md", "**/CLAUDE.md"]}))
    assert _refusal({"files": [f"d{i}/CLAUDE.md" for i in range(65)]})[0]["type"] == "too_long"
    assert len(_section().validate({"files": [f"d{i}/CLAUDE.md" for i in range(64)]}).files or ()) == 64


@pytest.mark.parametrize("name", ["node_modules", ".venv", "third_party", "gen-src", "a.b+c@d"])
def test_a_directory_is_one_name(name: str) -> None:
    assert _section().validate({"exclude_dirs": [name]}).exclude_dirs == (name,)


@pytest.mark.parametrize("name", ["", "a/b", "node_*", ".", "..", "node modules", "x" * 101])
def test_a_directory_that_is_not_one_name_is_refused_naming_the_key(name: str) -> None:
    errors = _refusal({"exclude_dirs": [name]})
    assert [str(e["loc"][0]) for e in errors] == ["exclude_dirs"], errors
    assert "each directory once" in str(_refusal({"exclude_dirs": ["vendor", "vendor"]}))


@pytest.mark.parametrize(
    ("raw", "accepted"),
    [
        ({"check_minutes": 15}, True),
        ({"check_minutes": 1440}, True),
        ({"check_minutes": 14}, False),
        ({"check_minutes": 1441}, False),
        ({"check_minutes": 15.0}, False),
        ({"read_concurrency": 1}, True),
        ({"read_concurrency": 32}, True),
        ({"read_concurrency": 0}, False),
        ({"read_concurrency": 33}, False),
    ],
)
def test_the_cadence_and_the_reads_at_once_are_bounded(raw: dict[str, Any], accepted: bool) -> None:
    if accepted:
        assert _section().validate(raw).model_dump()[next(iter(raw))] == next(iter(raw.values()))
    else:
        assert [str(e["loc"][0]) for e in _refusal(raw)] == list(raw), f"{raw} must be refused naming its key"


#: Rules an operator could write, with globs of every shape the section takes.
CUSTOM = {
    "files": [
        "*.md",
        "docs/**/*.md",
        "**/.kiro/steering/*.md",
        "a/**/b/**/c.md",
        "**/*rules",
        "*/x.md",
        ".github/*/*.md",
        "**/x+y@z.md",
        "**/CONVENTIONS.md",
        # No wildcard, each naming a directory: git lists every file under it, whatever its kind.
        ".kiro/steering",
        ".roo/rules",
        "docs/agents",
        # No wildcard, naming a file; and a wildcard glob a directory's name matches literally.
        "STYLE.md",
        "notes*",
    ],
    "exclude_dirs": ["third_party", ".hidden"],
}
#: Paths on both sides of each glob of :data:`CUSTOM`, and inside the directories it skips.
PATHS = [
    *("README.md", ".dotfile.md", "third_party.md", "CONVENTIONS.md", "docs/a.md", "docs/x/y/b.md", "docs/b.txt"),
    *(".kiro/steering/s.md", "p/.kiro/steering/s.md", "p/.kiro/steering/deep/s.md", "a/b/c.md", "a/x/b/y/c.md"),
    *("a/c.md", ".cursorrules", "sub/.clinerules", "rules", "x/rules/y", "q/x.md", "q/r/x.md", ".github/w/z.md"),
    *(".github/z.md", "deep/x+y@z.md", "x+y@z.md", "third_party/CONVENTIONS.md", "a/third_party/b/CONVENTIONS.md"),
    *("x/.hidden/CONVENTIONS.md", "docs/.hidden/a.md", "src/CONVENTIONS.md.bak", "src/conventions.md"),
    *(".kiro/steering/a.md", ".kiro/steering/img.png", ".kiro/steering/deep/b.txt", ".kiro/steeringx/c.md"),
    *("p/.kiro/steering/d.txt", ".roo/rules/a.md", ".roo/rules/sub/b.md", ".roo/rulesx/c.md"),
    *(".roo/rules/third_party/x.md", "docs/agents/one.md", "docs/agents/old/two.txt", "docs/agentsx.md"),
    *("STYLE.md", "STYLE.md.d/y.md", "a/STYLE.md", "notes*/a.md", "notesfile", "notes/b.md"),
]


def _git_lists(root: Path, command: str) -> list[str]:
    for path in PATHS:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("x\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    out = subprocess.run(["sh", "-c", command], cwd=root, capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


@pytest.mark.parametrize("exclude_dirs", [CUSTOM["exclude_dirs"], []], ids=["skipping", "skipping-nothing"])
def test_git_lists_exactly_the_files_the_schedule_selects_for_any_rules_the_section_accepts(
    tmp_path: Path, exclude_dirs: list[str]
) -> None:
    # The schedule fingerprints what FileRules.select picks; the auditor runs the task's git command. A glob
    # the two read apart would audit other files than the schedule watched, so the section takes none.
    from embry0.services.context_audit_command import list_command

    rules = _rules(_section().validate({**CUSTOM, "exclude_dirs": exclude_dirs}))
    selected = rules.select(PATHS)
    listed = _git_lists(tmp_path, list_command(rules))
    assert listed == selected, (
        f"git and the matcher disagree: git alone lists {sorted(set(listed) - set(selected))}, "
        f"the matcher alone selects {sorted(set(selected) - set(listed))}"
    )
    assert len(selected) > 10 and len(PATHS) - len(selected) > 5, f"the paths fall on both sides: {selected}"
    skipped = {
        "third_party/CONVENTIONS.md",
        "a/third_party/b/CONVENTIONS.md",
        "x/.hidden/CONVENTIONS.md",
        ".roo/rules/third_party/x.md",
    }
    assert (skipped <= set(selected)) is (not exclude_dirs), "the skipped directories decide those four"
    under = {".kiro/steering/a.md", ".kiro/steering/img.png", ".kiro/steering/deep/b.txt", ".roo/rules/sub/b.md"}
    assert under <= set(selected), "a glob that names a directory selects every file under it, as git lists it"
    assert {"docs/agents/old/two.txt", "STYLE.md", "notes*/a.md", "notesfile"} <= set(selected)
    near = {".kiro/steeringx/c.md", "p/.kiro/steering/d.txt", ".roo/rulesx/c.md", "STYLE.md.d/y.md", "a/STYLE.md"}
    assert not near & set(selected), "a directory glob selects only what lies under that directory"


# ---- layers ---------------------------------------------------------------------------


def test_the_files_are_a_repository_s_and_the_cadence_the_deployment_s() -> None:
    section = _section()
    assert section.settable("organization") == {"days", "open_prs", "files", "exclude_dirs"}
    assert section.settable("repository") == {"days", "open_prs", "files", "exclude_dirs"}, (
        "repositories differ: each may name its own"
    )
    assert {"check_minutes", "read_concurrency"} <= section.settable("deployment")


def test_an_organization_s_files_reach_its_repositories_and_a_repository_s_own_win() -> None:
    section = _section()
    snapshot = _snapshot(
        {
            "deployment": {"files": ["**/CLAUDE.md"], "exclude_dirs": ["vendor"]},
            "organization:acme": {"files": ["**/AGENTS.md", "**/CONVENTIONS.md"]},
            f"repository:{WIDGETS}": {"files": ["**/.kiro/steering/*.md"], "exclude_dirs": []},
        }
    )

    def read(repo: str) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
        model = snapshot.get(section, repo=repo)
        return model.files, model.exclude_dirs

    assert read(WIDGETS) == (("**/.kiro/steering/*.md",), ()), "a repository's own layer wins"
    assert read(GIZMOS) == (("**/AGENTS.md", "**/CONVENTIONS.md"), ("vendor",)), (
        "the organization's files reach its repositories, and a key it does not set stays the deployment's"
    )
    assert read(GADGETS) == (("**/CLAUDE.md",), ("vendor",)), "another organization reads the deployment's"
    steering = [".kiro/steering/a.md", "vendor/.kiro/steering/b.md"]
    assert _rules(snapshot.get(section, repo=WIDGETS)).select(steering) == steering, "no directory skipped"


def test_a_repository_s_null_reads_the_built_in_list_under_an_organization_that_names_its_own() -> None:
    snapshot = _snapshot(
        {"organization:acme": {"files": ["**/CONVENTIONS.md"]}, f"repository:{WIDGETS}": {"files": None}}
    )
    assert _rules(snapshot.get(_section(), repo=WIDGETS)) == _built_in(), "files: null in a layer is the built-in list"
    assert _rules(snapshot.get(_section(), repo=GIZMOS)).globs == ("**/CONVENTIONS.md",)


def test_an_organization_s_cadence_is_not_read() -> None:
    snapshot = _snapshot(
        {"deployment": {"check_minutes": 60}, "organization:acme": {"check_minutes": 15, "read_concurrency": 1}}
    )
    model = snapshot.get(_section(), repo=WIDGETS)
    assert (model.check_minutes, model.read_concurrency) == (60, 8), "a layer may not pace the deployment's schedule"


# ---- the environment ------------------------------------------------------------------


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


#: Every variable whose name says context audit: none of them is this section's.
AUDIT_VARIABLES = {
    "CONTEXT_AUDIT_JOB_BUDGET_USD": "5",
    "CONTEXT_AUDIT_MAX_JOBS_PER_TICK": "4",
    "CONTEXT_AUDIT_LOCAL_MODEL": "custom/audit",
}


def test_no_variable_governs_the_section() -> None:
    # The four keys were constants in the code, never variables: there is nothing to import.
    section = _section()
    assert section.env == {}, "no variable ever held the audit's files or cadence; one mapped here would invent one"
    config = _config(**AUDIT_VARIABLES)
    assert [v.section for v in governed_variables(CATALOG, config) if v.section == section.name] == []
    assert section.name not in env_values(CATALOG, config).values, "embry0 settings import-env writes nothing for it"


@pytest.mark.requires_postgres
async def test_the_boot_import_leaves_the_section_and_its_ledger_alone(db_with_migrations: DatabasePool) -> None:
    section = _section()
    store = SettingsStore(db_with_migrations, CATALOG)

    result = await import_env_once(store, _config(**AUDIT_VARIABLES))

    assert result.imported.get("budget"), "the context audit's own variables are still imported, into budget"
    row = await store.document()
    assert section.name not in row["data"], "the import wrote into the context_audit section"
    assert [key for key in row["bootstrapped_keys"] if key.startswith(f"{section.name}.")] == [], (
        "the ledger records no context_audit key, so a later release that adds a variable imports it once"
    )
    assert (await store.snapshot()).get(section) == section.defaults()


# ---- the schedule, the pull requests and the reserve (#170) ----------------------------------------


def _has(*keys: str) -> Section[Any]:
    section = _section()
    missing = [key for key in keys if key not in section.model.model_fields]
    if missing:
        pytest.fail(f"the context_audit section has no {missing}: they are still the organization row's, or nothing")
    return section


@pytest.mark.parametrize("days", [1, 7, 365, False, None])
def test_the_schedule_is_a_whole_number_of_days_or_off(days: Any) -> None:
    assert _has("days").validate({"days": days}).days == days


@pytest.mark.parametrize("days", [0, 366, -1, True, 1.5, "7"])
def test_a_schedule_out_of_range_is_refused_naming_the_key_alone(days: Any) -> None:
    errors = _refusal_of(_has("days"), {"days": days})
    assert {tuple(e["loc"]) for e in errors} == {("days",)}, (
        f"{days!r} must be refused as days, not as a union: {errors}"
    )
    if days in (0, 366, True):
        assert "from 1 to 365" in str(errors[0]["msg"]), errors


def test_pull_requests_are_on_or_off() -> None:
    section = _has("open_prs")
    assert section.validate({"open_prs": True}).open_prs is True
    assert section.defaults().open_prs is False, "off by default, as the organization setting was"
    for value in ("true", 1, None):
        assert [tuple(e["loc"])[0] for e in _refusal_of(section, {"open_prs": value})] == ["open_prs"]


def test_the_rate_reserve_is_a_bounded_share_and_the_deployment_s_alone() -> None:
    section = _has("rate_reserve_percent")
    assert section.defaults().rate_reserve_percent == 20, "20% of the limit by default: 1,000 of GitHub's 5,000"
    assert section.validate({"rate_reserve_percent": 0}).rate_reserve_percent == 0, "0 never defers"
    assert section.validate({"rate_reserve_percent": 90}).rate_reserve_percent == 90
    for value in (-1, 91, 1.5, "20"):
        assert [tuple(e["loc"])[0] for e in _refusal_of(section, {"rate_reserve_percent": value})] == [
            "rate_reserve_percent"
        ], value
    assert "rate_reserve_percent" not in section.settable("organization") | section.settable("repository")
    assert [tuple(e["loc"])[0] for e in _refusal_of(section, {"rate_reserve": 2000})] == ["rate_reserve"], (
        "the count this key replaced is not taken"
    )


def test_a_repository_s_false_turns_off_what_its_organization_turned_on() -> None:
    section = _has("days", "open_prs")
    snapshot = _snapshot(
        {
            "deployment": {"days": 30},
            "organization:acme": {"days": 7, "open_prs": True},
            f"repository:{WIDGETS}": {"days": False},
        }
    )
    widgets, gizmos = snapshot.get(section, repo=WIDGETS), snapshot.get(section, repo=GIZMOS)
    assert (widgets.days, widgets.open_prs) == (False, True), "its own schedule, its organization's pull requests"
    assert (gizmos.days, gizmos.open_prs) == (7, True)
    assert snapshot.get(section, repo=GADGETS).days == 30, "another organization reads the deployment's"


def _refusal_of(section: Section[Any], raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    with pytest.raises(SectionRefused) as refused:
        section.validate(raw)
    return refused.value.errors

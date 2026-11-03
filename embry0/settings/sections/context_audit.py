"""The ``context_audit`` section: which files the context audit reads, and how often its schedule looks.

An operator writes it the way they would say it::

    context_audit:
      files:                   # null, the default, reads the built-in list
        - "**/CLAUDE.md"
        - "**/AGENTS.md"
        - "**/CONVENTIONS.md"
        - ".roo/rules/**/*.md"
      exclude_dirs: [node_modules, vendor, dist]
      check_minutes: 60
      read_concurrency: 4

``files`` and ``exclude_dirs`` are a repository's own conventions, so an
organization's layer and a repository's may set each, and a layer replaces
the list it inherits whole. The schedule selects and fingerprints a
repository's files with them, and each audit's task lists the same files
with git (:mod:`embry0.services.context_audit_command`), so both read the
rules the repository resolves at that moment. How often the schedule looks
and how many repositories it reads at once pace the forge calls every
organization's audits make together, so they are the deployment's alone.

None of the four was a variable: each was a constant in the code, so there
is nothing to import from the environment, and a deployment that writes
nothing reads what it read before. What an audit may spend is the
``budget`` section's (``context_audit_job_usd``,
``context_audit_max_jobs_per_tick``), and whether an organization's
repositories are audited at all is its ``context_audit_days`` setting.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import Field, StringConstraints, field_validator

from embry0.services.instruction_files import (
    MAX_DIR_LENGTH,
    MAX_GLOB_LENGTH,
    MAX_GLOBS,
    MAX_SKIPPED_DIRS,
    SKIPPED_DIRS,
    FileRules,
    check_dir_name,
    check_glob,
)
from embry0.settings.registry import Section, SectionModel

#: The least ``check_minutes`` takes. The schedule reads the key again at
#: least this often while it waits, so a shorter interval applies within it.
CHECK_MINUTES_MIN: Final = 15

#: A glob as the section stores it; :func:`check_glob` checks the rest.
Glob = Annotated[str, StringConstraints(max_length=MAX_GLOB_LENGTH)]
#: A directory name as the section stores it; :func:`check_dir_name` checks the rest.
DirName = Annotated[str, StringConstraints(max_length=MAX_DIR_LENGTH)]


class ContextAuditSettings(SectionModel):
    files: tuple[Glob, ...] | None = Field(
        default=None,
        max_length=MAX_GLOBS,
        description=(
            "The instruction files the context audit reads in a repository, as globs over its tracked files: "
            "`**/` matches any number of directories, none included, and `*` any characters within one path "
            "segment. A glob holds only letters, digits, `.`, `_`, `-`, `+`, `@`, `*` and `/`, starts at the "
            "repository's root and never ends with `**`. A glob with no wildcard that names a directory, such "
            "as `.kiro/steering`, selects every tracked file under it, whatever its kind, as git does: write "
            "`.kiro/steering/*.md` for its Markdown alone. null reads the built-in list (docs/context-audit.md); "
            "a list replaces it whole, so name every file to read. A change that selects other files makes each "
            "repository it touches due once."
        ),
    )
    exclude_dirs: tuple[DirName, ...] | None = Field(
        default=None,
        max_length=MAX_SKIPPED_DIRS,
        description=(
            "Directories never searched for instruction files, each one name matched at any depth: vendored "
            "and generated trees, whose instruction files belong to another project. null reads the built-in "
            f"list ({', '.join(f'`{name}`' for name in sorted(SKIPPED_DIRS))}); a list replaces it whole, and "
            "[] searches every directory."
        ),
    )
    # A wake reads the forge: one commit listing per repository, and its tree
    # when the head moved. Once a quarter hour is short against an audit
    # interval of days; 15 minutes is the least, so the loop reads the key at
    # least that often while it waits.
    check_minutes: int = Field(
        default=CHECK_MINUTES_MIN,
        ge=CHECK_MINUTES_MIN,
        le=1440,
        description=(
            "Minutes between two looks of the schedule at the repositories of every organization it audits: "
            "one commit read per repository, and its tree when the head moved. A change applies within 15 "
            "minutes. The deployment's alone: it paces the forge calls of every organization's audits together."
        ),
    )
    # One after another, an organization with 150 repositories took 40 seconds
    # to read, longer than a browser waits; a handful at a time stays far below
    # the forge's limits.
    read_concurrency: int = Field(
        default=8,
        ge=1,
        le=32,
        description=(
            "Repositories of one organization read from the forge at once, by the schedule, by "
            "GET /orgs/{owner}/context-audit and by a run by hand. The deployment's alone."
        ),
    )

    @field_validator("files")
    @classmethod
    def _files(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("name at least one glob; null reads the built-in list")
        for position, glob in enumerate(value, start=1):
            try:
                check_glob(glob)
            except ValueError as exc:
                raise ValueError(f"glob {position}: {exc}") from None
        if len(set(value)) != len(value):
            raise ValueError("name each glob once")
        return value

    @field_validator("exclude_dirs")
    @classmethod
    def _exclude_dirs(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        for position, name in enumerate(value, start=1):
            try:
                check_dir_name(name)
            except ValueError as exc:
                raise ValueError(f"directory {position}: {exc}") from None
        if len(set(value)) != len(value):
            raise ValueError("name each directory once")
        return value


def file_rules(settings: ContextAuditSettings) -> FileRules:
    """The files ``settings`` select: its lists, the built-in one where a list is null."""
    return FileRules.of(settings.files, settings.exclude_dirs)


CONTEXT_AUDIT = Section(
    name="context_audit",
    model=ContextAuditSettings,
    summary=(
        "Which files the context audit reads in a repository, how often its schedule looks, and how many "
        "repositories it reads at once."
    ),
    # Which files steer an agent is a repository's own convention, so an
    # organization and a repository may each name theirs. How often the
    # schedule looks and how many repositories it reads at once bound the
    # forge calls every organization's audits make together, so they stay
    # the deployment's. No env: none of the four was ever a variable.
    org_keys=frozenset({"files", "exclude_dirs"}),
    repo_keys=frozenset({"files", "exclude_dirs"}),
)

"""The ``context_audit`` section: whether and how often a repository is audited, what an audit reads, and how the schedule paces the forge.

An operator writes it the way they would say it::

    context_audit:
      days: 7                  # audit every 7 days; null or false: off
      open_prs: true           # open a pull request from each audit's fixes
      files:                   # null, the default, reads the built-in list
        - "**/CLAUDE.md"
        - "**/AGENTS.md"
        - "**/CONVENTIONS.md"
        - ".roo/rules/**/*.md"
      exclude_dirs: [node_modules, vendor, dist]
      check_minutes: 60
      read_concurrency: 4
      rate_reserve_percent: 20

``days`` and ``open_prs`` were an organization's own settings,
``context_audit_days`` and ``context_audit_open_prs`` on its row (ADR 0020,
ADR 0022); the first start of the release that moved them copies each
organization's into its layer, once (:mod:`embry0.settings.org_import`), and
``PATCH /orgs/{owner}`` takes the old names for one release, writing them
into the layer. An organization's layer and a repository's may set both,
as they may ``files`` and ``exclude_dirs``, a repository's own conventions;
a layer replaces the list it inherits whole. The schedule selects and fingerprints a
repository's files with them, and each audit's task lists the same files
with git (:mod:`embry0.services.context_audit_command`), so both read the
rules the repository resolves at that moment. How often the schedule looks,
how many repositories it reads at once and the forge calls it leaves to
the jobs pace the forge calls every organization's audits make together,
so they are the deployment's alone.

No key here was ever a variable: the files and the pacing were constants in
the code, and ``days`` and ``open_prs`` lived on the organization row. So
there is nothing to import from the environment, and a deployment that
writes nothing reads what it read before. What an audit may spend is the
``budget`` section's (``context_audit_job_usd``,
``context_audit_max_jobs_per_tick``).
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

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

#: The bounds of ``days``, as the organization setting it replaced took them.
DAYS_MIN: Final = 1
DAYS_MAX: Final = 365

#: A glob as the section stores it; :func:`check_glob` checks the rest.
Glob = Annotated[str, StringConstraints(max_length=MAX_GLOB_LENGTH)]
#: A directory name as the section stores it; :func:`check_dir_name` checks the rest.
DirName = Annotated[str, StringConstraints(max_length=MAX_DIR_LENGTH)]


class ContextAuditSettings(SectionModel):
    days: Annotated[int, Field(ge=DAYS_MIN, le=DAYS_MAX)] | Literal[False] | None = Field(
        default=None,
        description=(
            "Days between a repository's scheduled audits: the schedule audits it when its instruction files "
            "change, and again this many days after its newest audit once its default branch moved. 1 to 365; "
            "false or null is off, the default, so nothing is audited until the deployment, an organization or "
            "a repository sets it. Written with PATCH, null takes the key out of a layer, which then reads the "
            "layer below, and false turns the schedule off in it. Was the organization's context_audit_days."
        ),
    )
    open_prs: bool = Field(
        default=False,
        description=(
            "An audit that finds outdated claims commits the fixes it checked on a branch of its own and opens a "
            "pull request for a person to review and merge (ADR 0022), one open at a time per repository; false "
            "leaves the findings in the job's report. Was the organization's context_audit_open_prs."
        ),
    )
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
    # GitHub answers a head read that has not changed with 304, which costs
    # nothing against its limit; a read that has changed costs one call and
    # its tree one more, and the jobs read and write with the same credential.
    # A share, not a count: 20% of GitHub's usual 5,000 is 1,000, below the
    # Slop-O-Meter's 2,000 and above merge sync's floor of 500, and a token
    # with a smaller limit keeps the same share.
    rate_reserve_percent: int = Field(
        default=20,
        ge=0,
        le=90,
        description=(
            "The share of a credential's forge rate limit (X-RateLimit-Limit), in percent, below which the "
            "schedule defers for the organizations reading with it: no paid read and no audit started until the "
            "window ends, so the last calls stay with the jobs that use the same credential. The window is the "
            "reset the forge names, at most an hour; with none it can use, 15 minutes, then one call asks again. "
            "A head read with an ETag still goes out, since a branch that did not move answers 304 for free. A "
            "run by hand and GET /orgs/{owner}/context-audit read no further either, and a run by hand starts "
            "nothing. A forge that sends no limit is never deferred, and 0 never defers. The deployment's alone."
        ),
    )

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, value: Any) -> Any:
        # Before the union, so every refusal is one message naming the key, and a 0 is never read as false.
        if value is None or value is False or (type(value) is int and DAYS_MIN <= value <= DAYS_MAX):
            return value
        raise ValueError(f"a whole number of days from {DAYS_MIN} to {DAYS_MAX}, or false or null for off")

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


def audit_days(settings: ContextAuditSettings) -> int | None:
    """The days between a repository's scheduled audits, or None when its schedule is off (false or null)."""
    days = settings.days
    return days if type(days) is int else None


CONTEXT_AUDIT = Section(
    name="context_audit",
    model=ContextAuditSettings,
    summary=(
        "Which files the context audit reads in a repository, how often its schedule looks, and how many "
        "repositories it reads at once."
    ),
    # Whether a repository is audited, how often and with a pull request,
    # and which files steer an agent there are its owner's choices: an
    # organization may set them for its repositories and a repository for
    # itself (one taken out with days: false, one audited more often). How
    # often the schedule looks, how many repositories it reads at once and
    # what it leaves of a credential's rate limit bound the forge calls every
    # organization's audits make together, so they stay the deployment's. No
    # env: no key was ever a variable.
    org_keys=frozenset({"days", "open_prs", "files", "exclude_dirs"}),
    repo_keys=frozenset({"days", "open_prs", "files", "exclude_dirs"}),
)

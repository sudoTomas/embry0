"""The ``slop_meter`` section: how the Slop-O-Meter weighs lines and attention, and how often it reads.

An operator writes it the way they would say it::

    slop_meter:
      interval_hours: 12          # score twice a day
      weights:                    # merged entry by entry over the defaults
        "*.sql": 1.0              # SQL is core logic here
        "db/migrations/**": 0     # generated migrations weigh nothing
      excluded_dirs: [vendor, node_modules, third_party, generated]
      lines_per_hour: 30          # a team that reads every line twice
      level_names: [Clean, Fine, Mixed, Sloppy, Slop]
      bots: [release-bot, ci@acme.example]

Every default is the published algorithm's value as the meter read it before
this section (docs/slop-meter.md credits it), so a deployment that writes
nothing scores exactly as it did. ``small_repository_in_full`` is the one
place embry0 departs from that algorithm on purpose, kept as data so an
organization can score as the algorithm is written.

**What a key changes.** Every key but ``enabled``, ``interval_hours``,
``api_calls_per_tick``, ``rate_reserve`` and ``judge_prompt``
(:data:`NOT_SCORED`) is what a score is computed from. Each score row records
:func:`config_version`, a digest of those keys as its repository read them;
a change to one of them changes the digest. Every five minutes the meter
compares each repository's stored digest with the one it reads now, and
rescores the repositories whose digests differ from what was read, with no
fetch and no forge call, without waiting for its next run. ``judge_prompt``
applies to the outlier commits judged after it changes: a judgment is kept
and never asked again.

**Layers.** What counts as code, how fast a team attends to it, where the
level bands fall, the judge's guidance and the owner's own bots are an
organization's to say for its repositories; what counts as code is also a
repository's own, for its layout (``weights``, ``excluded_dirs``,
``test_dirs``, ``test_files``, ``test_marks``). ``weights`` is merged entry
by entry, so a layer adds or changes the globs it names and keeps every
other. ``bots`` and ``ai_agents`` add to what a layer inherits, so a bot the
deployment lists counts for every organization and no layer takes one out;
``excluded_dirs`` and the ``test_*`` lists replace what they inherit, so a
repository can take back a directory the default excludes. A credential's
forge budget is the deployment's ``api_calls_per_tick`` and
``rate_reserve``; an organization's own values only narrow its own share of
it. Whether the meter runs and how often are the deployment's alone.

**Before this section** the reading pace was ``SLOP_METER_INTERVAL_SECONDS``
(``0`` turned the reading off), ``SLOP_METER_API_CALLS_PER_TICK`` and
``SLOP_METER_RATE_RESERVE``: the first start of this release imports them
once (``env`` below). An owner's own bots were ``slop_meter_bots`` on its
organization row (#184); the same start copies each into the organization's
layer as ``bots`` (:data:`embry0.services.organizations.MOVED_SETTINGS`).
Everything else was a constant in the meter's code.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Annotated, Any, Final

from pydantic import Field, StringConstraints, ValidationInfo, field_validator, model_validator

from embry0.services.slop_meter.globs import (
    GRAM,
    MAX_BROAD_GLOBS,
    MAX_DIR_LENGTH,
    MAX_GLOB_LENGTH,
    MAX_MARK_LENGTH,
    broad,
    check_dir_name,
    check_glob,
    check_mark,
)
from embry0.settings.registry import EnvImport, Section, SectionModel, refuse_unprintable

# ---- the defaults: the published algorithm's values, as the meter read them before this section ----

#: Core logic: a changed line weighs in full.
_CORE: Final = (
    "py", "pyi", "ts", "mts", "cts", "js", "mjs", "cjs", "go", "rs", "java", "kt", "kts", "scala", "c", "h",
    "cc", "cpp", "cxx", "hpp", "hh", "cs", "rb", "php", "swift", "m", "mm", "ex", "exs", "erl", "clj", "cljs",
    "hs", "ml", "fs", "lua", "pl", "pm", "r", "jl", "dart", "zig", "nim", "groovy", "sh", "bash", "zsh",
    "ps1", "tf", "sol", "proto", "graphql", "gql",
)  # fmt: skip
#: User interface code and templates.
_UI: Final = ("tsx", "jsx", "vue", "svelte", "astro", "html", "htm", "erb", "hbs", "ejs", "jinja", "j2", "twig", "liquid")  # fmt: skip
#: Styling.
_STYLE: Final = ("css", "scss", "sass", "less", "styl", "pcss")
#: Minified, bundled, source-map and declaration files: generated, so they weigh nothing.
_GENERATED: Final = ("*.min.js", "*.min.css", "*.bundle.js", "*.map", "*.d.ts")

DEFAULT_WEIGHTS: Final[dict[str, float]] = {
    **{f"*.{extension}": 1.0 for extension in _CORE},
    "*.sql": 0.8,
    **{f"*.{extension}": 0.5 for extension in _UI},
    **{f"*.{extension}": 0.3 for extension in _STYLE},
    **dict.fromkeys(_GENERATED, 0.0),
}
DEFAULT_EXCLUDED_DIRS: Final = ("vendor", "node_modules", "third_party", "third-party", "external", "deps", "bower_components")  # fmt: skip
DEFAULT_TEST_DIRS: Final = (
    "__tests__", "tests", "test", "spec", "specs", "examples", "example", "demos", "demo", "samples", "sample",
    "fixtures", "testdata",
)  # fmt: skip
DEFAULT_TEST_FILES: Final = ("*_test.go", "*_test.py", "*_test.rb", "*_test.ex", "*_test.exs", "*_spec.rb", "test_?*.py")  # fmt: skip
DEFAULT_TEST_MARKS: Final = ("test", "spec", "stories", "story")
DEFAULT_LEVEL_BOUNDS: Final = (0.2, 0.4, 0.6, 0.8)
DEFAULT_LEVEL_NAMES: Final = ("Buttered Noodles", "Spaghetti Bolognese", "Lasagna", "Sloppy Joe", "Just The Slop")
DEFAULT_JUDGE_PROMPT: Final = """\
You judge one large commit of a software repository: did it add bulk code no
person wrote or reviewed line by line (slop, such as a large AI-generated
change), or is its size explained by something else (not slop: the project's
initial import, a code drop moved in from elsewhere, a restructuring that moved
files, a vendored or generated library, a data or fixture dump)?
Judge from what the message and the files say. When it is unclear, answer
slop: false only if the files themselves look moved, vendored or generated."""

#: The number of levels. Five, as the published algorithm reads a score; their bounds and names are data.
LEVELS: Final = 5
#: How often the meter reads by default, and the least ``interval_hours`` takes: a quarter hour.
DEFAULT_INTERVAL_HOURS: Final = 24.0
MIN_INTERVAL_HOURS: Final = 0.25
#: The most entries ``weights`` holds.
MAX_WEIGHTS: Final = 1000
#: The most entries ``bots`` and ``ai_agents`` hold once every layer has added its own: twice what
#: ``slop_meter_bots`` held on the organization row.
MAX_IDENTITIES: Final = 200
MAX_IDENTITY_LENGTH: Final = 200
MAX_LEVEL_NAME_LENGTH: Final = 60
MAX_JUDGE_PROMPT_LENGTH: Final = 4000

#: The keys a score is not computed from: whether and how often the meter reads, its forge budget, and the
#: guidance of judgments still to come. :func:`config_version` digests every other key.
NOT_SCORED: Final = frozenset({"enabled", "interval_hours", "api_calls_per_tick", "rate_reserve", "judge_prompt"})

Glob = Annotated[str, StringConstraints(max_length=MAX_GLOB_LENGTH)]
DirName = Annotated[str, StringConstraints(max_length=MAX_DIR_LENGTH)]
Mark = Annotated[str, StringConstraints(max_length=MAX_MARK_LENGTH)]
Identity = Annotated[str, StringConstraints(max_length=MAX_IDENTITY_LENGTH)]
LevelName = Annotated[str, StringConstraints(max_length=MAX_LEVEL_NAME_LENGTH)]


def enabled_from_env(value: Any) -> bool:
    """``SLOP_METER_INTERVAL_SECONDS`` as the loop read it: ``0`` (or less) turned the reading off."""
    return float(value) > 0


def interval_from_env(value: Any) -> float:
    """``SLOP_METER_INTERVAL_SECONDS`` in hours, at least a quarter hour; ``0``, which turned the reading off and
    is ``enabled: false`` now, reads as the default, so nothing is imported for it."""
    seconds = float(value)
    if seconds <= 0:
        return DEFAULT_INTERVAL_HOURS
    return max(MIN_INTERVAL_HOURS, seconds / 3600)


def _checked(values: tuple[str, ...], check: Any, what: str) -> tuple[str, ...]:
    """``values`` when ``check`` takes each."""
    for position, value in enumerate(values, start=1):
        try:
            check(value)
        except ValueError as exc:
            raise ValueError(f"{what} {position}: {exc}") from None
    return values


def _each(values: tuple[str, ...], check: Any, what: str) -> tuple[str, ...]:
    """``values`` when ``check`` takes each and no two are the same without regard to case."""
    _checked(values, check, what)
    if len({value.lower() for value in values}) != len(values):
        raise ValueError(f"name each {what} once")
    return values


def _few_broad(globs: Iterable[str], info: ValidationInfo) -> None:
    """Refuse, on a write, more broad globs than :data:`MAX_BROAD_GLOBS`: each is checked against every path.

    Checked when a write is stored (it carries a validation context), with what
    the key inherits, never when a stored value is read: a document below that
    grows later never makes a layer's globs read as refused.
    """
    if info.context is None:
        return
    count = sum(1 for glob in globs if broad(glob))
    if count > MAX_BROAD_GLOBS:
        raise ValueError(
            f"{count} globs, with the ones inherited, name no literal directory, no extension and no "
            f"{GRAM} literal characters in a row (such as */* or *x*); at most {MAX_BROAD_GLOBS}, since each is "
            "checked against every path"
        )


def _identity(text: str) -> str:
    if not text.strip():
        raise ValueError("write a name, an address or a login, not blanks")
    if text != text.strip():
        raise ValueError("an identity has no blanks around it")
    refuse_unprintable(text, what="an identity")
    return text


def _agent(text: str) -> str:
    local, at, domain = text.partition("@")
    if not local or not at or "@" in domain or any(char.isspace() for char in text):
        raise ValueError(
            "an AI agent is its commit address up to and including the @, such as aider@, or a whole address, "
            "with no blanks"
        )
    refuse_unprintable(text, what="an AI agent's address")
    return text


class SlopMeterSettings(SectionModel):
    # ---- when it reads ----
    enabled: bool = Field(
        default=True,
        description=(
            "The meter reads the repositories enrolled in background.slop_meter and scores them. false stops the "
            "reading and the scoring everywhere; what was kept of a repository that left the scope is still "
            "removed after background.prune_after_days. Was SLOP_METER_INTERVAL_SECONDS=0. The deployment's alone."
        ),
    )
    interval_hours: float = Field(
        default=DEFAULT_INTERVAL_HOURS,
        ge=MIN_INTERVAL_HOURS,
        le=8760,
        description=(
            "Hours between two runs over every enrolled repository: fetch, read the next part of its history and "
            "events, and rescore it once caught up. While a history is still being read the meter comes back "
            "sooner (SLOP_METER_CATCHUP_SECONDS). A change applies within five minutes. At least a quarter hour. "
            "Was SLOP_METER_INTERVAL_SECONDS. The deployment's alone."
        ),
    )
    api_calls_per_tick: int = Field(
        default=200,
        ge=0,
        le=10_000,
        description=(
            "Forge API calls one run may make with one credential, shared round-robin by the repositories read "
            "with it, to read comments, reviews and logins: the deployment's value is the credential's budget. "
            "An organization's value only narrows its own repositories' share of it, never widens it, and never "
            "lowers another organization's on the same credential. Git fetches are not API calls."
        ),
    )
    rate_reserve: int = Field(
        default=2_000,
        ge=0,
        le=100_000,
        description=(
            "The rate limit a credential keeps for the jobs that use it: once its remaining calls "
            "(X-RateLimit-Remaining) drop below this, the run makes no further call with it. An organization's "
            "larger value stops only its own repositories' reads sooner; a smaller one changes nothing."
        ),
    )
    # ---- what a changed line weighs ----
    weights: dict[Glob, float] = Field(
        default_factory=lambda: dict(DEFAULT_WEIGHTS),
        max_length=MAX_WEIGHTS,
        description=(
            "What one changed line of a file weighs, by glob, from 0 to 10; a file no glob matches weighs 0, so "
            "data, documents and binaries never count. A glob with no '/' matches a file's name in any directory "
            "(*.py), one with a '/' its path from the repository's root (db/migrations/*.sql, generated/**); '*' "
            "matches within one name, '?' one character, '**' any directories. Where several match, the most "
            "specific wins: a glob with a '/' over any without one; between two with one, more segments free of "
            "wildcards, then fewer ** segments, then fewer other segments with a wildcard, then more segments, "
            "then more literal characters; between two without, more literal characters (*.min.js over *.js); "
            "then the first in alphabetical order. So generated/** weighs every file under generated/, whatever "
            "its kind, and src/main.py wins over src/**/main.py. At most 16 globs that name no literal "
            "directory, no extension and no three literal characters in a row (*/*, *x*). Write globs in lowercase, quoted: matching ignores case, "
            "and YAML reads a bare * as an alias. Merged entry by entry: a document adds or changes the globs it "
            "names and keeps every other, so a glob set to 0 stops counting; at most 1000 globs in all, counted "
            "with the defaults and what a write inherits."
        ),
    )
    excluded_dirs: tuple[DirName, ...] = Field(
        default=DEFAULT_EXCLUDED_DIRS,
        max_length=100,
        description=(
            "Directories whose files weigh nothing, each one name matched at any depth: vendored and third-party "
            "code. A list replaces the one it inherits whole; [] excludes none."
        ),
    )
    test_dirs: tuple[DirName, ...] = Field(
        default=DEFAULT_TEST_DIRS,
        max_length=100,
        description=(
            "Directories whose files are tests or examples, each one name matched at any depth: their lines weigh "
            "test_weight of what weights says. A list replaces the one it inherits whole."
        ),
    )
    test_files: tuple[Glob, ...] = Field(
        default=DEFAULT_TEST_FILES,
        max_length=200,
        description=(
            "Globs of test and example files, in the grammar of weights (*_test.go, test_?*.py): their lines weigh "
            "test_weight of what weights says. A list replaces the one it inherits whole."
        ),
    )
    test_marks: tuple[Mark, ...] = Field(
        default=DEFAULT_TEST_MARKS,
        max_length=50,
        description=(
            "Words that mark a test or example file when they come just before its extension: test marks "
            "a.test.ts and b.test.py, but not a.test.helper.ts. Their lines weigh test_weight of what weights "
            "says. A list replaces the one it inherits whole."
        ),
    )
    test_weight: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="A test or example file's lines weigh this share of what their file's glob in weights says.",
    )
    # ---- the codebase's size ----
    bootstrap_lines: int = Field(
        default=5_000,
        ge=1,
        le=10_000_000,
        description=(
            "Size dampening: while a codebase holds fewer weighted lines than this, a week's lines cost in "
            "proportion to its size, so a young project's first lines cost little; from this size on, in full."
        ),
    )
    complexity_weight: float = Field(
        default=0.05,
        ge=0,
        le=1,
        description=(
            "Size dampening: how much more a line costs as the codebase grows, per doubling past 100,000 weighted "
            "lines (the cost is min(1, size / bootstrap_lines) + complexity_weight × log2(1 + size / 100,000))."
        ),
    )
    # ---- the attention people spend ----
    commit_hours: float = Field(
        default=1.0,
        gt=0,
        le=24,
        description="Hours of a person's attention each commit they author implies, before its time.",
    )
    subcommit_hours: float = Field(
        default=1.0,
        ge=0,
        le=24,
        description=(
            "Hours more for each commit a squash merge folded in (a body that lists two or more bullet lines)."
        ),
    )
    coauthor_shares: tuple[float, ...] = Field(
        default=(0.5, 0.25, 0.125),
        min_length=1,
        max_length=10,
        description=(
            "Each human Co-authored-by trailer implies this share of commit_hours, in trailer order; the last "
            "share applies to every co-author after it. Each from 0 to 1."
        ),
    )
    ai_coauthor_share: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="A commit with an AI co-author implies this share of the hours its author's commit would.",
    )
    event_hours: float = Field(
        default=1.0,
        ge=0,
        le=24,
        description="Hours of attention each comment, review comment and review on the forge implies (GitHub only).",
    )
    lines_per_hour: float = Field(
        default=40.0,
        gt=0,
        le=10_000,
        description="Weighted lines one hour of a person's attention covers.",
    )
    weekly_hours_cap: float = Field(
        default=80.0,
        gt=0,
        le=168,
        description=(
            "The most hours one person's week counts, on a concave curve (h − h³/(3·cap²)), so overtime counts "
            "for less and nothing past the cap."
        ),
    )
    # ---- who counts as the core team ----
    core_min_commits: int = Field(
        default=10,
        ge=0,
        le=100_000,
        description=(
            "Authors with fewer human commits than this are drive-bys: their time counts for nothing. Their share "
            "rises to full at core_full_commits, and up to a quarter more for a large share of the history."
        ),
    )
    core_full_commits: int = Field(
        default=60,
        ge=1,
        le=100_000,
        description="The human commits at which an author's time counts in full. More than core_min_commits.",
    )
    small_repository_in_full: bool = Field(
        default=True,
        description=(
            "When no author has reached core_min_commits, everyone's time counts in full: a small repository has "
            "no core team to call anyone a drive-by, and read literally every author of it counts for nothing "
            "and all its code reads as slop. embry0's departure from the published algorithm; false scores such "
            "a repository as the algorithm is written."
        ),
    )
    # ---- outlier commits ----
    outlier_min_lines: int = Field(
        default=2_000,
        ge=1,
        le=10_000_000,
        description=(
            "Net weighted lines a commit must add to be an outlier, judged by a model on "
            "background_models.slop_judge: slop, or a code drop, an import or a move that is not."
        ),
    )
    outliers_per_year: int = Field(
        default=4,
        ge=0,
        le=365,
        description=(
            "Outlier commits judged per year of the project's age, the largest first; at least one however young."
        ),
    )
    judge_prompt: str = Field(
        default=DEFAULT_JUDGE_PROMPT,
        min_length=1,
        max_length=MAX_JUDGE_PROMPT_LENGTH,
        description=(
            "What the model judging an outlier commit is told slop is and is not. The answer's form (JSON with "
            "slop and a reason) is added after it. Applies to commits judged after it changes: a judgment is "
            "kept and never asked again, so a change rescores nothing."
        ),
    )
    # ---- what a score reads as ----
    level_bounds: tuple[float, ...] = Field(
        default=DEFAULT_LEVEL_BOUNDS,
        min_length=LEVELS - 1,
        max_length=LEVELS - 1,
        description=(
            "Where the five levels part: a score below the first bound is level 1, below the second level 2, "
            "and so on; at or above the last, level 5. Four bounds, each above the one before, from 0 to 1."
        ),
    )
    level_names: tuple[LevelName, ...] = Field(
        default=DEFAULT_LEVEL_NAMES,
        min_length=LEVELS,
        max_length=LEVELS,
        description="The five levels' names, level 1 (the least slop) first. Each one line, each named once.",
    )
    # ---- who is not a person ----
    bots: tuple[Identity, ...] = Field(
        default=(),
        max_length=MAX_IDENTITIES,
        description=(
            "Bots, as commit names, addresses or forge logins matched whole without regard to case: their commits "
            "add lines but no attention, and as a co-author they count as an AI agent does. Beyond the ones every "
            "deployment knows ([bot] accounts, noreply@ addresses, known coding agents) and embry0's own commit "
            "identities. Each document adds to the list it inherits, so a bot the deployment lists counts for "
            "every organization; no layer takes one out, and a bot spelled otherwise is the same one. At most 200 "
            "in all, counted with what a write inherits. Was the organization's slop_meter_bots."
        ),
    )
    ai_agents: tuple[Identity, ...] = Field(
        default=(),
        max_length=MAX_IDENTITIES,
        description=(
            "AI coding agents beyond the known ones, each its commit address up to and including the @ (aider@, "
            "any domain) or a whole address (agent@tools.example), matched without regard to case: a commit one "
            "authors adds lines but no attention, and one it co-authors counts ai_coauthor_share of its author's "
            "hours. Each document adds to the list it inherits, as bots. At most 200 in all, counted with what a write "
            "inherits."
        ),
    )

    @field_validator("weights")
    @classmethod
    def _weights(cls, value: dict[str, float], info: ValidationInfo) -> dict[str, float]:
        for position, (glob, weight) in enumerate(value.items(), start=1):
            try:
                check_glob(glob)
            except ValueError as exc:
                raise ValueError(f"glob {position}: {exc}") from None
            if not 0 <= weight <= 10:
                raise ValueError(f"glob {position}: a weight is from 0 to 10")
        _few_broad(value, info)
        return value

    @field_validator("excluded_dirs", "test_dirs")
    @classmethod
    def _dirs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _each(value, check_dir_name, "directory")

    @field_validator("test_files")
    @classmethod
    def _test_files(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        _each(value, check_glob, "glob")
        _few_broad(value, info)
        return value

    @field_validator("test_marks")
    @classmethod
    def _test_marks(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _each(value, check_mark, "mark")

    @field_validator("coauthor_shares")
    @classmethod
    def _shares(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0 <= share <= 1 for share in value):
            raise ValueError("each share is from 0 to 1")
        return value

    @field_validator("level_bounds")
    @classmethod
    def _bounds(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not 0 < bound <= 1 for bound in value):
            raise ValueError("each bound is above 0 and at most 1, as a score is")
        if any(later <= earlier for earlier, later in zip(value, value[1:], strict=False)):
            raise ValueError("each bound is above the one before it")
        return value

    @field_validator("level_names")
    @classmethod
    def _names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for position, name in enumerate(value, start=1):
            if not name.strip() or name != name.strip():
                raise ValueError(f"level {position}: write a name, with no blanks around it")
            try:
                refuse_unprintable(name, what="a level's name")
            except ValueError as exc:
                raise ValueError(f"level {position}: {exc}") from None
        if len({name.lower() for name in value}) != len(value):
            raise ValueError("name each level once")
        return value

    @field_validator("judge_prompt")
    @classmethod
    def _prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("write what the judge is told, not blanks")
        if any(
            unicodedata.category(char) in ("Cf", "Zl", "Zp")
            or (unicodedata.category(char) == "Cc" and char not in "\n\t")
            for char in value
        ):
            raise ValueError("the prompt is plain text: lines and tabs, no control, format or separator character")
        return value

    @field_validator("bots")
    @classmethod
    def _bots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _checked(value, _identity, "bot")

    @field_validator("ai_agents")
    @classmethod
    def _ai_agents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _checked(value, _agent, "AI agent")

    @model_validator(mode="after")
    def _core_ramp(self) -> SlopMeterSettings:
        if self.core_full_commits <= self.core_min_commits:
            raise ValueError("core_full_commits is not more than core_min_commits: the ramp needs room to rise")
        return self


def config_version(settings: SlopMeterSettings) -> str:
    """A digest of every key a score is computed from (all but :data:`NOT_SCORED`), as a repository reads them.

    The same for the same keys and values in any process: the keys and the
    entries of ``weights`` are sorted, and every value is written as the
    model dumps it to JSON. A release that adds a key to what a score is
    computed from changes every digest, so its first run rescores every
    repository. Each score row records it.
    """
    values = {key: value for key, value in settings.model_dump(mode="json").items() if key not in NOT_SCORED}
    text = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


#: The section's keys an organization may set: all but whether the meter runs and how often.
_ORG_KEYS: Final = frozenset(SlopMeterSettings.model_fields) - {"enabled", "interval_hours"}
#: What counts as code in one repository's layout.
_REPO_KEYS: Final = frozenset({"weights", "excluded_dirs", "test_dirs", "test_files", "test_marks"})
_INTERVAL_VARIABLE: Final = "slop_meter_interval_seconds"

SLOP_METER = Section(
    name="slop_meter",
    model=SlopMeterSettings,
    summary=(
        "How the Slop-O-Meter weighs a changed line and a person's attention, where its levels fall, which "
        "identities are bots, and how often and how hard it reads the forge."
    ),
    # What counts as code, how fast a team attends to it, where its levels
    # fall, its judge's guidance and its own bots are an organization's to
    # say; what counts as code is a repository's too, for its layout. The
    # forge budget is per credential, which an organization reads with.
    # Whether the meter runs and how often are the deployment's alone.
    org_keys=_ORG_KEYS,
    repo_keys=_REPO_KEYS,
    merged_keys=frozenset({"weights"}),
    # A bot or an agent the deployment knows is one in every organization: each layer adds to the lists.
    added_keys=frozenset({"bots", "ai_agents"}),
    env={
        "enabled": EnvImport(_INTERVAL_VARIABLE, enabled_from_env),
        "interval_hours": EnvImport(_INTERVAL_VARIABLE, interval_from_env),
        "api_calls_per_tick": "slop_meter_api_calls_per_tick",
        "rate_reserve": "slop_meter_rate_reserve",
    },
)

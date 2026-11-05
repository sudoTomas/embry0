"""Run embry0's own git commands in a sandbox on embry0's own configuration.

embry0 runs git in a job's sandbox for its own steps: the clone, the salvage
commit and the rescue push, the changed-file diff, a review's checkout, the
verify gate, an areas or audit delivery. Those commands run as the agent user,
in the agent's workspace, and git would read everything the agent can write:
``~/.gitconfig``, ``~/.config/git/config``, ``.git/config`` and whatever it
includes, ``.git/hooks``, attributes that select a filter, diff or merge
driver, a nested repository's own configuration. An agent with file tools and
no shell could then choose a program embry0's command runs. This module is the
one way embry0 builds and runs such a command, and none of them reads a program
from anything the agent can write:

* **embry0's configuration** is a root-owned file, :data:`CONFIG_PATH`, which
  :func:`configure` writes as root from stdin; ``GIT_CONFIG_GLOBAL`` points
  at it, so the agent's ``~/.gitconfig`` and XDG config are never read. The
  system configuration (``/etc/gitconfig``, root-owned) is trusted.
* **Pinned keys** (:data:`PINS`) are set at command scope, which outranks
  every file: hooks, the fsmonitor, signing, credential helpers, editors,
  pagers and submodule recursion are off. The environment
  (:data:`ENVIRONMENT`) limits transports to http and https and empties
  ``GIT_ASKPASS``.
* **The repository's own configuration** is read, never rewritten: before a
  step the script checks that ``.git`` is a real directory with no
  ``commondir``, and that every key in ``.git/config`` (read without its
  includes, which runs nothing) is in a family that runs nothing
  (:data:`INERT`) or a pinned key. Anything else refuses the step, naming the
  keys, with exit :data:`REFUSED_EXIT`: :class:`SandboxGitRefused`.
* **Nested repositories** are never entered: status and diff carry
  ``--ignore-submodules=all``, fetch and push ``--no-recurse-submodules``,
  every submodule name a ``.gitmodules`` carries (in the working tree, the
  index or HEAD) gets ``submodule.<name>.ignore=all``, and ``add`` leaves
  out every gitlink in the index, since git add checks a gitlink by starting
  git inside it whatever the submodule settings say.

A step's git command is rendered by :func:`git` as a call to the shell
function ``embry0_git``, which only :func:`script`'s prelude defines, so a
rendered command run without the prelude fails instead of running on the
agent's configuration. ``tests/unit/execution/test_sandbox_git_guard.py``
refuses a git command written anywhere else in embry0.

The agent's own git is untouched: its ``~/.gitconfig`` still gets the
git-proxy rewrite, the bearer and the identity (:func:`configure`), and its
local configuration and hooks keep working for the commands it runs itself.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from embry0.sandbox.github.git_ops import ProxyRoute

logger = structlog.get_logger(__name__)

#: The workspace the job's repository is cloned into.
WORKSPACE = "/workspace"
#: embry0's own git configuration in a sandbox: root-owned, written by :func:`configure`.
CONFIG_PATH = "/run/embry0/git/config"
#: The exit status of a step the prelude refused.
REFUSED_EXIT = 86
#: The first word of the line the prelude writes to stderr when it refuses.
REFUSED_MARK = "embry0-git-refused:"
#: The oldest git that honours ``GIT_CONFIG_GLOBAL`` (2.32) and ``GIT_CONFIG_COUNT`` (2.31).
MIN_GIT = (2, 32)
#: The page that explains a refusal to an operator.
DOCS = "docs/sandbox-git.md"


class GitStep(StrEnum):
    """The embry0 step a git command belongs to: a log field and the refusal counter's label."""

    CONFIGURE = "configure"
    CLONE = "clone"
    QA_CHECKOUT = "qa_checkout"
    BRANCH_PROBE = "branch_probe"
    SALVAGE_COMMIT = "salvage_commit"
    RESCUE_PUSH = "rescue_push"
    BASE_BRANCH = "base_branch"
    DEFAULT_BRANCH = "default_branch"
    CHANGED_FILES = "changed_files"
    DIFF_LINES = "diff_lines"
    AREAS_DELIVERY = "areas_delivery"
    AUDIT_DELIVERY = "audit_delivery"
    REVIEW_CHECKOUT = "review_checkout"
    VERIFY_GATE = "verify_gate"


@dataclass(frozen=True)
class KeyFamily:
    """Config keys matched by ``pattern`` (git's lower-case name, ``*`` for any subsection or rest)."""

    pattern: str
    why: str

    def matches(self, key: str) -> bool:
        return fnmatch.fnmatchcase(key, self.pattern)


def _family(patterns: str, why: str) -> tuple[KeyFamily, ...]:
    return tuple(KeyFamily(p, why) for p in patterns.split())


# Each family was checked against git 2.39's documentation (Documentation/config/)
# and source: nothing in it names a program, a hook, a driver or a file of
# configuration, and nothing in it makes git start one. A key git uses only for
# a command embry0 never runs is still left out when its value is a program
# (help.browser), so that "allowed" always means "never executed".
#: Families of keys a repository's own config may carry: none of them runs anything.
INERT: tuple[KeyFamily, ...] = (
    *_family("advice.* color.* column.*", "hints, colours and column layout"),
    *_family("help.*", "help.autoCorrect and help.format; help.browser is excepted below"),
    *_family("i18n.*", "encodings, converted in-process"),
    *_family("init.*", "init.defaultBranch; init.templateDir is excepted below"),
    *_family("gc.*", "garbage-collection thresholds and expiries; gc runs git itself, with the pins"),
    *_family("rerere.*", "rerere.enabled and rerere.autoUpdate"),
    *_family("status.*", "status display; status.submoduleSummary is pinned off"),
    *_family("pull.*", "git pull only, which embry0 never runs; strategies resolve on git's exec path"),
    *_family("push.default push.autosetupremote", "which branch a push names"),
    *_family("fetch.prune fetch.prunetags", "pruning of remote-tracking refs"),
    *_family("rebase.*", "git rebase only; instructionFormat is a log format, not a command"),
    *_family("merge.conflictstyle merge.ff merge.log", "how a merge is recorded"),
    *_family("branch.*", "upstream tracking, descriptions, sort order and merge options"),
    *_family("diff.algorithm diff.renames diff.colormoved diff.mnemonicprefix", "diff display"),
    *_family("log.*", "log display; log.showSignature is pinned off"),
    *_family("user.*", "identity; user.signingKey is used only for signing, which is pinned off"),
    # lfs.* is NOT inert: git-lfs reads lfs.extension.<x>.{clean,smudge},
    # lfs.customtransfer.<x>.path and lfs.standalonetransferagent from .git/config
    # and runs them. The whole family is refused; the git-proxy refuses LFS anyway.
    *_family("index.* pack.* feature.* maintenance.*", "index and pack formats, scheduled maintenance tasks"),
    *_family(
        "commit.template blame.ignorerevsfile core.excludesfile checkout.defaultremote push.followtags "
        "http.postbuffer safe.directory submodule.active",
        "names a file to read or a value to use; runs nothing (http.postbuffer is a buffer size, not a URL)",
    ),
    *_family(
        "core.repositoryformatversion core.filemode core.bare core.logallrefupdates core.ignorecase "
        "core.precomposeunicode core.symlinks core.autocrlf core.eol core.safecrlf core.quotepath "
        "core.longpaths core.untrackedcache core.whitespace core.abbrev core.commentchar core.checkstat "
        "core.trustctime core.sparsecheckout core.sparsecheckoutcone",
        "the repository format and how the working tree is compared",
    ),
    *_family(
        "remote.*.url remote.*.pushurl remote.*.fetch remote.*.tagopt remote.*.prune remote.*.prunetags "
        "remote.*.mirror remote.*.skipdefaultupdate remote.*.promisor remote.*.partialclonefilter "
        "remote.*.gh-resolved",
        "where a remote is and which refs it maps; transports are limited to http and https, and "
        "remote.origin.mirror is pinned off",
    ),
    *_family("submodule.*.url submodule.*.active", "written by git submodule init; recursion is pinned off"),
    # extensions.worktreeConfig is allowed: the prelude reads .git/config.worktree
    # and checks its keys against this same allowlist.
    *_family("extensions.*", "repository format extensions"),
    *_family("diff.noprefix", "a boolean; diff display"),
)

#: Keys inside an :data:`INERT` family that are refused all the same.
EXCEPTED: tuple[KeyFamily, ...] = (
    KeyFamily("help.browser", "names the browser program git help --web starts"),
    KeyFamily("init.templatedir", "names the directory whose hooks and config a new repository copies"),
)

#: Keys embry0 sets at command scope, which outranks every config file, and their values.
PINS: tuple[tuple[str, str], ...] = (
    ("core.hooksPath", "/dev/null"),
    ("core.fsmonitor", "false"),
    ("core.askPass", ""),
    ("core.editor", ":"),
    ("sequence.editor", ":"),
    ("core.pager", "cat"),
    ("core.attributesFile", "/dev/null"),
    # An empty helper clears the list, URL-scoped helpers included.
    ("credential.helper", ""),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("push.gpgSign", "false"),
    ("log.showSignature", "false"),
    ("merge.verifySignatures", "false"),
    ("submodule.recurse", "false"),
    ("fetch.recurseSubmodules", "false"),
    ("push.recurseSubmodules", "no"),
    ("diff.ignoreSubmodules", "all"),
    ("status.submoduleSummary", "false"),
    # A mirror remote rewrites the push refspec and breaks embry0's push to
    # origin; embry0 only ever uses origin, so pin it off there (a mirror on
    # another remote is inert and allowed).
    ("remote.origin.mirror", "false"),
)

#: Pinned keys a repository's own config may carry: the pin wins. ``credential.helper``'s
#: empty pin also clears every ``credential.<url>.helper``.
PINNED: tuple[KeyFamily, ...] = (
    *(KeyFamily(key.lower(), "pinned at command scope") for key, _ in PINS),
    KeyFamily("credential.*.helper", "cleared by the empty credential.helper pin"),
)

#: The environment of every embry0 git command: it outranks the matching config keys.
#: ``GIT_CONFIG_GLOBAL`` is not listed here; :func:`prelude` is its one source,
#: since it points at the per-call config path.
ENVIRONMENT: tuple[tuple[str, str], ...] = (
    # Every other transport (ext::, fd::, file, ssh, git) is refused, whatever
    # protocol.*.allow a config file says.
    ("GIT_ALLOW_PROTOCOL", "http:https"),
    ("GIT_TERMINAL_PROMPT", "0"),
    # Set and empty: git then skips core.askPass and SSH_ASKPASS.
    ("GIT_ASKPASS", ""),
    ("GIT_EDITOR", ":"),
    ("GIT_SEQUENCE_EDITOR", ":"),
    ("GIT_PAGER", "cat"),
    # git status writes no index, so no index-change hook is ever due.
    ("GIT_OPTIONAL_LOCKS", "0"),
)

#: Subcommands rendered as a prelude function of their own instead of ``embry0_git``.
_FUNCTIONS: Mapping[str, str] = {"add": "embry0_git_add"}

#: Flags added after a subcommand, where a ``.gitmodules`` could otherwise outrank a pin.
SUBCOMMAND_FLAGS: Mapping[str, tuple[str, ...]] = {
    "status": ("--ignore-submodules=all",),
    "diff": ("--no-ext-diff", "--no-textconv"),
    "fetch": ("--no-recurse-submodules",),
    "push": ("--no-recurse-submodules",),
}


def local_key_allowed(key: str) -> bool:
    """Whether a repository's own config may carry ``key`` (git's canonical, lower-case name)."""
    if any(f.matches(key) for f in EXCEPTED):
        return False
    return any(f.matches(key) for f in INERT) or any(f.matches(key) for f in PINNED)


# ---- rendering ----------------------------------------------------------------


class Shell(str):
    """A word :func:`git` passes through unquoted: a shell variable or substitution the script defines."""

    __slots__ = ()


_SUBCOMMAND = re.compile(r"^[a-z][a-z-]*$")


def _words(subcommand: str, args: tuple[str, ...], options: Mapping[str, str] | None) -> tuple[str, list[str]]:
    """The function that runs ``subcommand`` and its arguments: the safety flags added, nothing quoted yet."""
    if not _SUBCOMMAND.fullmatch(subcommand):
        raise ValueError(f"not a git subcommand: {subcommand!r}")
    function = _FUNCTIONS.get(subcommand, "embry0_git")
    if options and subcommand in _FUNCTIONS:
        raise ValueError(f"git {subcommand} takes no per-command options")
    words: list[str] = []
    for key, value in (options or {}).items():
        lowered = key.lower()
        if not any(f.matches(lowered) for f in INERT) or any(f.matches(lowered) for f in EXCEPTED):
            raise ValueError(f"-c {key} is not a key embry0's git may set per command")
        words += ["-c", f"{key}={value}"]
    if subcommand not in _FUNCTIONS:
        words.append(subcommand)
    words += list(SUBCOMMAND_FLAGS.get(subcommand, ()))
    return function, [*words, *args]


def git(subcommand: str, *args: str, options: Mapping[str, str] | None = None) -> str:
    """Shell text for one git command embry0 runs: ``embry0_git <subcommand> <flags> <args>``.

    Every argument is quoted unless it is a :class:`Shell`. ``options`` become
    ``-c key=value``; they may only name keys in an :data:`INERT` family,
    since ``-c`` would outrank the pins.
    """
    function, words = _words(subcommand, args, options)
    return " ".join([function, *(w if isinstance(w, Shell) else shlex.quote(w) for w in words)])


def check_repo(path: str = WORKSPACE) -> str:
    """Shell text that checks the repository at ``path`` and changes into it (the prelude's function)."""
    return f"embry0_git_repo {shlex.quote(path)}"


def _case_arm(families: tuple[KeyFamily, ...]) -> str:
    patterns = [f.pattern for f in families]
    for p in patterns:
        if not re.fullmatch(r"[a-z0-9.*-]+", p):
            raise ValueError(f"unsafe key pattern: {p!r}")
    return "|".join(patterns)


_PRELUDE = r"""embry0_git_refuse() {
  printf '%s %s\n' '@MARK@' "$*" >&2
  exit @EXIT@
}
embry0_git() { command git "$@"; }
# git add checks every gitlink in the index by starting git status inside it,
# whatever the submodule settings say (add_files_to_cache overrides them), and
# that git reads the nested repository's own config. Every gitlink is left out.
embry0_git_add() {
  embry0_git_gitlinks=()
  while IFS= read -r -d '' embry0_git_entry; do
    case "$embry0_git_entry" in
      "160000 "*) embry0_git_gitlinks+=(":(top,exclude,literal)${embry0_git_entry#*$'\t'}") ;;
    esac
  done < <(command git ls-files -s -z 2>/dev/null)
  command git add "$@" "${embry0_git_gitlinks[@]}"
}
# Start outside any repository: the prelude's own git calls (version, the
# identity read) and a repo=None body (the agent's ~/.gitconfig, a clone) must
# not discover /workspace's .git. A repo step cd's back in via embry0_git_repo.
cd / || embry0_git_refuse "no_root_dir /"
embry0_git_version=$(command git version 2>/dev/null) || embry0_git_refuse "git_too_old none"
embry0_git_version=${embry0_git_version#git version }
embry0_git_major=${embry0_git_version%%.*}
embry0_git_minor=${embry0_git_version#*.}
embry0_git_minor=${embry0_git_minor%%.*}
case "$embry0_git_major:$embry0_git_minor" in
  *[!0-9:]*|:*|*:) embry0_git_refuse "git_too_old $embry0_git_version" ;;
esac
if [ "$embry0_git_major" -lt @MIN_MAJOR@ ] || { [ "$embry0_git_major" -eq @MIN_MAJOR@ ] && [ "$embry0_git_minor" -lt @MIN_MINOR@ ]; }; then
  embry0_git_refuse "git_too_old $embry0_git_version"
fi
embry0_git_owned() {
  if [ -L "$1" ]; then embry0_git_refuse "config_not_root_owned $1"; fi
  embry0_git_stat=$(stat -c '%u %a' -- "$1" 2>/dev/null) || embry0_git_refuse "config_missing $1"
  [ "${embry0_git_stat%% *}" = "@OWNER@" ] || embry0_git_refuse "config_not_root_owned $1"
  [ $(( 8#${embry0_git_stat#* } & 8#022 )) -eq 0 ] || embry0_git_refuse "config_writable $1"
}
if [ ! -f @CONFIG@ ]; then embry0_git_refuse "config_missing "@CONFIG@; fi
embry0_git_owned @CONFIG@
embry0_git_owned @CONFIG_DIR@
embry0_git_owned @CONFIG_PARENT@
@EXPORTS@
export GIT_CONFIG_COUNT=0
embry0_git_pin() {
  export "GIT_CONFIG_KEY_$GIT_CONFIG_COUNT=$1" "GIT_CONFIG_VALUE_$GIT_CONFIG_COUNT=$2"
  GIT_CONFIG_COUNT=$((GIT_CONFIG_COUNT + 1))
}
@PINS@
# The author of embry0's own commits is embry0's, from its root-owned config,
# pinned at command scope so a local .git/config user.* cannot choose it.
embry0_git_pin_from_config() {
  embry0_git_v=$(command git config --file @CONFIG@ --no-includes --get "$1" 2>/dev/null) || return 0
  [ -n "$embry0_git_v" ] && embry0_git_pin "$1" "$embry0_git_v"
}
embry0_git_pin_from_config user.name
embry0_git_pin_from_config user.email
embry0_git_repo() {
  embry0_git_dir=$1/.git
  if [ -L "$embry0_git_dir" ] || [ ! -d "$embry0_git_dir" ]; then embry0_git_refuse "not_a_repository $1"; fi
  if [ -e "$embry0_git_dir/commondir" ] || [ -L "$embry0_git_dir/commondir" ]; then
    embry0_git_refuse "commondir $embry0_git_dir/commondir"
  fi
  embry0_git_names=$(cd / && command git config --file "$embry0_git_dir/config" --no-includes --name-only --list 2>/dev/null) \
    || embry0_git_refuse "config_unreadable $embry0_git_dir/config"
  # extensions.worktreeConfig makes git read .git/config.worktree too (sparse
  # checkout writes it): check its keys against the same allowlist.
  if [ -f "$embry0_git_dir/config.worktree" ]; then
    embry0_git_wt=$(cd / && command git config --file "$embry0_git_dir/config.worktree" --no-includes --name-only --list 2>/dev/null) \
      || embry0_git_refuse "config_unreadable $embry0_git_dir/config.worktree"
    embry0_git_names="$embry0_git_names
$embry0_git_wt"
  fi
  embry0_git_bad=
  while IFS= read -r embry0_git_key; do
    [ -n "$embry0_git_key" ] || continue
    case "$embry0_git_key" in
      @EXCEPTED@) embry0_git_bad="$embry0_git_bad $embry0_git_key" ;;
      @ALLOWED@) ;;
      *) embry0_git_bad="$embry0_git_bad $embry0_git_key" ;;
    esac
  done <<< "$embry0_git_names"
  [ -z "$embry0_git_bad" ] || embry0_git_refuse "keys$embry0_git_bad"
  cd -- "$1" || embry0_git_refuse "not_a_repository $1"
  embry0_git_mods=$({
    if [ -f .gitmodules ]; then command git config --file .gitmodules --no-includes --name-only --list; fi
    command git config --blob :.gitmodules --no-includes --name-only --list
    command git config --blob HEAD:.gitmodules --no-includes --name-only --list
  } 2>/dev/null)
  while IFS= read -r embry0_git_key; do
    case "$embry0_git_key" in
      submodule.*.*)
        embry0_git_key=${embry0_git_key#submodule.}
        embry0_git_pin "submodule.${embry0_git_key%.*}.ignore" all
        ;;
    esac
  done <<< "$embry0_git_mods"
}
"""


def prelude(*, config_path: str = CONFIG_PATH, owner_uid: int = 0) -> str:
    """The shell every embry0 git script starts with.

    ``config_path`` and ``owner_uid`` exist for tests, which cannot create a
    root-owned file; production uses the defaults.
    """
    config_dir = config_path.rsplit("/", 1)[0]
    config_parent = config_dir.rsplit("/", 1)[0]
    env = dict(ENVIRONMENT, GIT_CONFIG_GLOBAL=config_path)
    exports = "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    pins = "\n".join(f"embry0_git_pin {shlex.quote(k)} {shlex.quote(v)}" for k, v in PINS)
    replacements = {
        "@MARK@": REFUSED_MARK,
        "@EXIT@": str(REFUSED_EXIT),
        "@MIN_MAJOR@": str(MIN_GIT[0]),
        "@MIN_MINOR@": str(MIN_GIT[1]),
        "@OWNER@": str(int(owner_uid)),
        "@CONFIG@": shlex.quote(config_path),
        "@CONFIG_DIR@": shlex.quote(config_dir),
        "@CONFIG_PARENT@": shlex.quote(config_parent),
        "@EXPORTS@": exports,
        "@PINS@": pins,
        "@EXCEPTED@": _case_arm(EXCEPTED),
        "@ALLOWED@": _case_arm(INERT + PINNED),
    }
    text = _PRELUDE
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    return text


def script(body: str, *, repo: str | None = WORKSPACE, config_path: str = CONFIG_PATH, owner_uid: int = 0) -> list[str]:
    """The exec argv of a git script embry0 runs in a sandbox: ``bash -c '<prelude><check><body>'``.

    With ``repo``, the repository there is checked (:func:`check_repo`) and
    the script runs in it. Without, the body checks one itself when it needs
    to, as a clone does when it finds a checkout already there.
    """
    check = f"{check_repo(repo)}\n" if repo else ""
    return ["bash", "-c", f"{prelude(config_path=config_path, owner_uid=owner_uid)}{check}{body}"]


def command(subcommand: str, *args: str, repo: str = WORKSPACE, options: Mapping[str, str] | None = None) -> list[str]:
    """The exec argv of one git command embry0 runs in the repository at ``repo``.

    The arguments follow the script as its positional parameters, so each one
    reaches git as one word without quoting.
    """
    if any(isinstance(a, Shell) for a in args):
        raise ValueError("command() passes its arguments verbatim; a Shell word belongs in script()")
    function, words = _words(subcommand, args, options)
    return [*script(f'{function} "$@"', repo=repo), "embry0-git", *words]


# ---- refusals -------------------------------------------------------------------

_REASONS = {
    "git_too_old": "the sandbox's git is older than 2.32, which embry0 needs to run git on its own configuration",
    "config_missing": "embry0's own git configuration is missing from the sandbox",
    "config_not_root_owned": "embry0's own git configuration is not owned by root",
    "config_writable": "embry0's own git configuration can be written by someone other than root",
    "not_a_repository": "the workspace's .git is not a directory (a symlink, a file or nothing)",
    "commondir": "the workspace's .git names another directory as its common directory",
    "config_unreadable": "the workspace's .git/config could not be read",
    "keys": "the repository's own git config sets keys that can run a program or that embry0 does not know",
    "no_root_dir": "the sandbox has no root directory to run git from",
}

_MAX_KEYS = 20
_KEY_CHARS = re.compile(r"[^\x21-\x7e]")


@dataclass(frozen=True)
class Refusal:
    """Why the prelude refused a step: a reason code and, for ``keys``, the key names (never values)."""

    reason: str
    keys: tuple[str, ...] = ()
    detail: str = ""

    def explain(self) -> str:
        text = _REASONS.get(self.reason, f"refused ({self.reason})")
        if self.keys:
            text += ": " + ", ".join(self.keys)
            text += (
                ". Remove them from /workspace/.git/config in the sandbox (git config --unset-all <key>), "
                "or from wherever a setup script writes them"
            )
        elif self.reason.startswith("config_"):
            text += ". Re-enrolling or recreating the sandbox writes it again"
        return f"{text} (see {DOCS})"


def parse_refusal(text: str) -> Refusal | None:
    """The refusal a failed step's stderr carries, or None when the prelude did not refuse it."""
    for line in str(text or "").splitlines():
        start = line.find(REFUSED_MARK)
        if start < 0:
            continue
        words = line[start + len(REFUSED_MARK) :].split()
        if not words:
            return Refusal("unknown")
        reason = words[0] if words[0] in _REASONS else "unknown"
        if reason == "keys":
            keys = tuple(_KEY_CHARS.sub("?", w)[:100] for w in words[1 : 1 + _MAX_KEYS])
            if len(words) - 1 > _MAX_KEYS:
                keys += (f"and {len(words) - 1 - _MAX_KEYS} more",)
            return Refusal(reason, keys=keys)
        return Refusal(reason, detail=_KEY_CHARS.sub("?", " ".join(words[1:]))[:200])
    return None


class SandboxGitRefused(RuntimeError):
    """embry0 did not run a git step in a sandbox: the prelude refused it (:class:`Refusal`)."""

    def __init__(self, step: GitStep | str, refusal: Refusal) -> None:
        self.step = str(step)
        self.refusal = refusal
        super().__init__(f"embry0 did not run git in the sandbox ({self.step}): {refusal.explain()}")


_refusal_observer: Callable[[str], None] | None = None


def set_refusal_observer(observer: Callable[[str], None] | None) -> None:
    """``observer(step)`` is called on every refusal (the metrics counter). It must not raise."""
    global _refusal_observer
    _refusal_observer = observer


def _record(step: str, refusal: Refusal, *, job_id: str | None, container: str) -> None:
    logger.warning(
        "sandbox_git_refused",
        job_id=job_id,
        step=step,
        container=container,
        reason=refusal.reason,
        keys=list(refusal.keys),
        detail=refusal.detail or None,
    )
    observer = _refusal_observer
    if observer is not None:
        try:
            observer(step)
        except Exception:  # noqa: BLE001 - a metric must never fail a step
            logger.warning("sandbox_git_refusal_metric_failed", exc_info=True)


async def run(
    docker: Any,
    container: str,
    argv: list[str],
    *,
    step: GitStep,
    timeout: int = 60,
    job_id: str | None = None,
    env: dict[str, str] | None = None,
    stdin_data: bytes | None = None,
) -> str:
    """Run a :func:`script` or :func:`command` argv in ``container``; its stdout.

    Raises :class:`SandboxGitRefused` (a RuntimeError) when the prelude
    refused the step, after logging it and counting it; any other failure as
    ``docker.run_cmd`` raises it.
    """
    if argv[:2] != ["bash", "-c"] or REFUSED_MARK not in argv[2]:
        raise ValueError("sandbox_git.run takes an argv built by sandbox_git.script or sandbox_git.command")
    kwargs: dict[str, Any] = {}
    if env:
        kwargs["env"] = env
    exec_kwargs: dict[str, Any] = {"timeout": timeout}
    if stdin_data is not None:
        kwargs["interactive"] = True
        exec_kwargs["stdin_data"] = stdin_data
    try:
        return str(await docker.run_cmd(docker.build_exec_cmd(container, argv, **kwargs), **exec_kwargs))
    except RuntimeError as exc:
        refusal = parse_refusal(str(exc))
        if refusal is None:
            raise
        _record(str(step), refusal, job_id=job_id, container=container)
        raise SandboxGitRefused(step, refusal) from exc


def refusal_of(exc: BaseException | None) -> SandboxGitRefused | None:
    """``exc`` or the refusal in its cause chain, else None."""
    seen = 0
    while exc is not None and seen < 10:
        if isinstance(exc, SandboxGitRefused):
            return exc
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


# ---- embry0's configuration and the agent's -------------------------------------


def _quote_value(value: str) -> str:
    if any(c in value for c in "\n\r\0"):
        raise ValueError("a git config value cannot hold a line break or NUL")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_text(*, route: ProxyRoute | None, sandbox_token: str, identity: Any | None) -> str:
    """embry0's own git configuration for a sandbox: the identity, and the git-proxy rewrite and bearer."""
    lines = [
        "# embry0's own git configuration for this sandbox. Root-owned: embry0's git",
        "# commands read it in place of the agent's ~/.gitconfig.",
    ]
    if identity is not None:
        lines += ["[user]", f"\tname = {_quote_value(identity.name)}", f"\temail = {_quote_value(identity.email)}"]
    if route is not None:
        lines += [
            f"[url {_quote_value(route.proxy_base + route.repo + '.git')}]",
            f"\tinsteadOf = {_quote_value(route.rewrite_value)}",
            f"[http {_quote_value(route.proxy_base)}]",
            f"\textraHeader = {_quote_value('Authorization: Bearer ' + sandbox_token)}",
        ]
    return "\n".join(lines) + "\n"


#: Writes stdin to :data:`CONFIG_PATH` as root: a root-owned directory nobody else can write, the file replaced whole.
WRITE_CONFIG_ARGV: list[str] = [
    "sh",
    "-c",
    "set -e; umask 022; f=" + shlex.quote(CONFIG_PATH) + "; d=${f%/*}; p=${d%/*}; "
    'for x in "$p" "$d"; do if [ -L "$x" ]; then echo "$x is a symlink" >&2; exit 1; fi; '
    'mkdir -p "$x"; chmod 0755 "$x"; [ "$(stat -c %u "$x")" = 0 ]; done; '
    't=$(mktemp "$d/.config.XXXXXX"); cat > "$t"; chmod 0644 "$t"; mv -f "$t" "$f"',
]


def agent_config_body(route: ProxyRoute, identity: Any | None) -> str:
    """The script body that gives the agent's own git the git-proxy and the identity, in ~/.gitconfig.

    The bearer is read from stdin, so it is never on a ``docker exec``
    command line. A credential helper an older orchestrator installed is
    removed (``--unset-all`` exits 5 when there is none).
    """
    f = Shell('"$HOME/.gitconfig"')
    unset = git("config", "--file", f, "--unset-all", "credential.helper")
    header = Shell('"Authorization: Bearer $embry0_bearer"')
    steps = [
        'IFS= read -r embry0_bearer || [ -n "$embry0_bearer" ]',
        f'{{ {unset}; rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 5 ]; }}',
        git("config", "--file", f, "--replace-all", route.rewrite_key, route.rewrite_value),
        git("config", "--file", f, "--replace-all", route.header_key, header),
    ]
    if identity is not None:
        steps += [
            git("config", "--file", f, "user.email", identity.email),
            git("config", "--file", f, "user.name", identity.name),
        ]
    return " && ".join(steps)


async def configure(
    docker: Any,
    container: str,
    *,
    route: ProxyRoute | None,
    sandbox_token: str = "",
    identity: Any | None,
    agent_identity: bool = True,
    job_id: str | None = None,
) -> None:
    """Write embry0's own git configuration (root-owned) and, with a git-proxy, the agent's ``~/.gitconfig``.

    embry0's file always gets ``identity`` and, with ``route``, the git-proxy
    rewrite and the bearer; it is replaced whole, so a re-enroll's new bearer
    replaces the old. With ``route``, the agent's file gets the same rewrite
    and bearer, and the identity unless ``agent_identity`` is False (a
    re-enroll leaves the agent's identity as it is). Without a git-proxy the
    agent's file is left alone, as it always was. Secrets go on stdin only.
    """
    from embry0.sandbox.github.git_ops import check_sandbox_token

    if route is not None:
        check_sandbox_token(sandbox_token)
    text = config_text(route=route, sandbox_token=sandbox_token, identity=identity)
    await docker.run_cmd(
        docker.build_exec_cmd(container, list(WRITE_CONFIG_ARGV), interactive=True, user="root"),
        timeout=15,
        stdin_data=text.encode(),
    )
    if route is None:
        return
    # The agent's own ~/.gitconfig is best-effort: embry0's git uses the
    # root-owned file written above, so a shell-less agent that turned
    # ~/.gitconfig into a directory (or a .git/config git trips over) must not
    # fail create or resume (#353 review). The prelude's `cd /` keeps this off
    # /workspace's repo; any failure is logged and swallowed.
    try:
        await run(
            docker,
            container,
            script(agent_config_body(route, identity if agent_identity else None), repo=None),
            step=GitStep.CONFIGURE,
            timeout=15,
            job_id=job_id,
            stdin_data=f"{sandbox_token}\n".encode(),
        )
    except (RuntimeError, TimeoutError) as exc:
        logger.warning(
            "sandbox_git_agent_config_failed",
            job_id=job_id,
            container=container,
            error=str(exc)[:300],
            note="the agent's own git config was not written; embry0's own git uses its root-owned config",
        )

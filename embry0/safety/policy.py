"""Safety policy as data — the single source of truth for agent tool gating.

Three concentric rings defend the sandbox:

1. Ring 1 (container): Docker isolation — ephemeral, non-root, network-restricted.
   Unchanged by this module; documented here as context.
2. Ring 2 (declarative): deny_rules, Claude Code permission rules on paths.
   The SDK executor hands them to the CLI on its command line, which holds
   them for the whole session, and writes them into .claude/settings.json;
   its PreToolUse hook applies them too (gate_tool_call). Executors without
   the CLI apply them with evaluate_ring2_paths.
3. Ring 3 (programmable): content_checks — regex-on-input rules this module
   evaluates via evaluate_policy(). Used when Ring 2 can't express a check.

Fail-closed: if evaluate_policy raises or returns something malformed, the tool
is denied. A hook that errors silently and allows the tool through is a
security bug.
"""

from __future__ import annotations

import json
import os
import pwd
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final

from embry0.safety.bash_paths import BashPaths, BashSyntaxError, bash_paths
from embry0.safety.command_checks import DANGEROUS_COMMANDS, SECRET_FILES, reads_any, runs_any
from embry0.safety.path_patterns import PathRef, could_match
from embry0.safety.skill_commands import SKILL_TOOLS, SkillError, SkillSnapshot, skill_call, skill_commands


@dataclass(frozen=True)
class ContentRule:
    """A Ring-3 regex pattern evaluated against a tool's input string.

    - pattern: raw regex string (searched case-insensitively, normalized input).
    - tools: list of tool names this rule applies to; empty means all tools.
    - reason: human-readable explanation shown to Claude when the rule fires.
    """

    pattern: str
    tools: list[str]
    reason: str


@dataclass(frozen=True)
class CommandCheck:
    """A Ring-3 rule on what a shell command does, read from its parse (embry0.safety.command_checks).

    - runs: command names refused wherever the command runs one, at any depth
      (``docker-*`` matches a prefix);
    - reads: file names refused wherever the command reads a file by that name.
    """

    runs: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class Verdict:
    """Result of evaluating a policy against a single tool call."""

    allowed: bool
    reason: str = ""

    @classmethod
    def allow(cls) -> Verdict:
        return cls(allowed=True, reason="")

    @classmethod
    def deny(cls, reason: str) -> Verdict:
        return cls(allowed=False, reason=reason)


@dataclass(frozen=True)
class SafetyPolicy:
    """Full policy struct consumed by both executors.

    allowed_tools is passed to ClaudeAgentOptions.allowed_tools (SDK) or
    --allowedTools (CLI), and evaluate_policy checks tool names against it:
    None checks no names, and an empty list allows no tool but the CLI's own
    (_INFRA_ALLOWED_TOOLS). deny_rules go to the CLI as --disallowedTools and
    into settings.json, and gate_tool_call applies them at hook time, with
    content_checks (evaluate_policy).
    network_egress_allowlist is not enforced by this module — it is produced
    for consumption by sandbox container setup. EMB-29 enforces it for
    egress-enabled QA sandboxes via per-sandbox DOCKER-USER rules in DinD
    (see embry0/execution/runtime/egress.py); deployed-target QA derives its
    allowlist automatically from frontend_url + profile extra_hosts.
    """

    allowed_tools: list[str] | None = None
    deny_rules: list[str] = field(default_factory=list)
    content_checks: list[ContentRule] = field(default_factory=list)
    network_egress_allowlist: list[str] = field(default_factory=list)
    command_checks: list[CommandCheck] = field(default_factory=list)


def _normalize(text: str) -> str:
    """NFKC-normalize and collapse whitespace to defeat obfuscation, as patterns.normalize_command does."""
    from embry0.safety.patterns import normalize_command

    return normalize_command(text)


def _stringify_input(tool_input: dict[str, Any]) -> str:
    """Flatten all input values into one scanable string."""
    parts: list[str] = []
    for value in tool_input.values():
        try:
            parts.append(str(value))
        except Exception:
            continue
    return " ".join(parts)


# Harness-internal tools the Claude CLI drives itself (todo tracking, skill
# and slash-command invocation). Agent tool lists never enumerate them, so
# the EMB-37 name check must exempt them — denying them burns agent turns
# for zero safety value (they touch no external surface).
_INFRA_ALLOWED_TOOLS: Final[frozenset[str]] = frozenset({"TodoWrite", "Skill", "SlashCommand"})

# The CLI tools whose input runs as a shell command: Bash, and Monitor (CLI
# 2.1.316 and later), which runs a command in the background and streams its
# output. An agent whose operator-given tools (its definition's, or its
# template step's) name none of them gets no MCP server that runs a command
# either (embry0.agents.mcp_servers). A content rule scoped to any of them
# applies to all of them, and so does the Ring-2 check of the paths a command
# names. A CLI release that adds another such tool must add it here
# (test_ring2_cli_parity checks the CLI's tool list).
SHELL_TOOLS: Final[frozenset[str]] = frozenset({"Bash", "Monitor"})


# The longest input a content rule or the Ring-2 shell check reads. A content
# rule's regex can take time quadratic in its input (a pattern like
# ``cat\s+.*credentials`` on a long run of "cat "), so an input past this, to a tool
# a content rule checks, is refused rather than read; so is a longer shell
# command for the Ring-2 check. 64 KiB is far past any command an agent types.
MAX_CHECKED_INPUT: Final[int] = 64 * 1024


def _too_long(tool_name: str, length: int) -> str:
    return (
        f"Blocked by safety policy: the {tool_name} input is {length} characters long, past the "
        f"{MAX_CHECKED_INPUT} embry0 checks; split it, or write long text to a file with the Write tool"
    )


def _rule_applies(rule: ContentRule, tool_name: str) -> bool:
    """Whether a content rule checks ``tool_name``: a rule for one shell tool checks every shell tool."""
    if not rule.tools or tool_name in rule.tools:
        return True
    return tool_name in SHELL_TOOLS and not SHELL_TOOLS.isdisjoint(rule.tools)


def evaluate_policy(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str = "/workspace",
    home: str | None = None,
) -> Verdict:
    """Evaluate a single tool call against the tool-name allowlist and the
    Ring-3 content checks.

    Name check (EMB-37): when ``policy.allowed_tools`` is a list, any tool
    name outside it (and outside ``_INFRA_ALLOWED_TOOLS``) is denied, so an
    empty list allows none. This is the enforcement layer that survives
    runtime tool loading (ToolSearch/deferred tools) — the SDK's
    ``allowed_tools`` option is an auto-approve list, not an availability
    gate, so without this check an agent can call tools it discovered at
    runtime. ``allowed_tools=None`` skips name enforcement (content checks
    only).

    Content checks: a rule applies to the tools it names, and a rule that
    names a shell tool applies to every tool in ``SHELL_TOOLS``, so the Bash
    patterns also check Monitor's command. Command checks apply to every
    shell tool: the command is parsed (embry0.safety.bash_paths, run in ``cwd``
    by a user whose home is ``home``), and a command it runs or a file it reads
    that a check names refuses it, and so does a command that cannot be
    parsed.

    Returns Verdict.allow() if no rule fires. Returns Verdict.deny(reason)
    if any matching rule hits. On any internal exception, returns
    Verdict.deny("safety hook failed: ...") — fail-closed.
    """
    try:
        allowed = policy.allowed_tools
        if allowed is not None and tool_name not in allowed and tool_name not in _INFRA_ALLOWED_TOOLS:
            return Verdict.deny(f"tool {tool_name!r} is not in this agent's allowlist")
        rules = [rule for rule in policy.content_checks if _rule_applies(rule, tool_name)]
        checks = policy.command_checks if tool_name in SHELL_TOOLS else []
        if not rules and not checks:
            return Verdict.allow()
        raw = _stringify_input(tool_input)
        haystack = _normalize(raw) if len(raw) <= MAX_CHECKED_INPUT else raw
        if len(haystack) > MAX_CHECKED_INPUT:
            return Verdict.deny(_too_long(tool_name, len(haystack)))
        for rule in rules:
            if re.search(rule.pattern, haystack, re.IGNORECASE):
                return Verdict.deny(f"Blocked by safety policy: {rule.reason}")
        command = tool_input.get("command")
        if checks and isinstance(command, str) and command:
            return _command_checks(checks, tool_name, command, cwd=cwd, home=home or os.path.expanduser("~"))
        return Verdict.allow()
    except Exception as exc:
        return Verdict.deny(f"safety hook failed: {exc!r}")


@lru_cache(maxsize=64)
def _parsed(command: str, cwd: str, home: str) -> BashPaths | BashSyntaxError:
    """The command's parse, shared by the Ring-3 command checks and the Ring-2 check, or why it has none."""
    try:
        return bash_paths(command, cwd=cwd, home=home)
    except BashSyntaxError as exc:
        return exc


def _command_checks(checks: list[CommandCheck], tool_name: str, command: str, *, cwd: str, home: str) -> Verdict:
    found = _parsed(command, cwd, home)
    if isinstance(found, BashSyntaxError):
        return Verdict.deny(f"Blocked by safety policy: the {tool_name} command cannot be checked: {found}")
    for check in checks:
        ran = runs_any(found.commands, check.runs)
        if ran is not None:
            return Verdict.deny(f"Blocked by safety policy: the command runs {ran!r}: {check.reason}")
        read = reads_any(found.reads, check.reads)
        if read is not None:
            return Verdict.deny(f"Blocked by safety policy: the command reads {read.path!r}: {check.reason}")
    return Verdict.allow()


# ---- Default policies and rendering ----

# Ring-2 rules are Claude Code permission rules, passed verbatim to the CLI
# (--disallowedTools and settings.json). Claude Code anchors a rule's path by
# how it starts (#508, verified against CLI 2.1.292):
#
#   //path   the filesystem root: ``Read(//etc/**)`` is /etc/**
#   ~/path   the home directory
#   /path    the PROJECT ROOT, the directory the CLI runs in (/workspace), so
#            ``Read(/etc/**)`` means /workspace/etc/** and leaves /etc readable
#   path     unanchored: matched gitignore-style, at any depth (unused here)
#
# So every rule below that names an absolute path starts with ``//``.
# rule_path_to_absolute() is the one reading of these anchors in embry0.
#
# Claude Code matches no file path against a Write(path), Glob(path) or
# Grep(path) rule. A Read(path) rule refuses every file tool on its paths
# (reads, searches and writes), and an Edit(path) rule refuses Edit and Write.
# So in the CLI the Write/Glob/Grep rules below are inert, and the Read or Edit
# rule on the same path enforces them; evaluate_ring2_paths applies all of them.

# Ring-2 baseline: deny host-sensitive reads/writes globally.
_BASELINE_DENY_RULES: list[str] = [
    "Read(//etc/**)",
    "Read(//root/**)",
    "Read(~/.claude/**)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.gnupg/**)",
    "Read(//proc/**)",
    "Write(//etc/**)",
    "Write(//root/**)",
    "Write(//usr/**)",
    "Write(//bin/**)",
    "Write(//sbin/**)",
    "Edit(//etc/**)",
    "Edit(//root/**)",
    "Edit(//usr/**)",
    "Edit(//bin/**)",
    "Edit(//sbin/**)",
    # The sandbox must not rewrite the files that carry its permissions and
    # hooks: settings.json, settings.local.json and any settings.*.json. The
    # rest of .claude/ (skills, commands, agents) is the repo's own code, and
    # agents may edit it.
    "Write(//workspace/.claude/settings*.json)",
    "Edit(//workspace/.claude/settings*.json)",
    # Glob/Grep twins of the Read rules: inert in the CLI (see above), applied by evaluate_ring2_paths
    "Glob(//etc/**)",
    "Glob(//root/**)",
    "Glob(~/.claude/**)",
    "Glob(~/.ssh/**)",
    "Glob(~/.aws/**)",
    "Glob(~/.gnupg/**)",
    "Glob(//proc/**)",
    "Grep(//etc/**)",
    "Grep(//root/**)",
    "Grep(~/.claude/**)",
    "Grep(~/.ssh/**)",
    "Grep(~/.aws/**)",
    "Grep(~/.gnupg/**)",
    "Grep(//proc/**)",
]

# Agent-type → tools mapping (matches current developer/triage/review semantics).
# Custom agent types (agent_definitions rows) take the developer default.
_AGENT_TOOLS: dict[str, list[str]] = {
    "triage": ["Read", "Glob", "Grep"],
    "developer": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    "review": ["Read", "Glob", "Grep", "Bash"],
}

# What an agent keeps of its own list when a run-time change empties it:
# triage narrowing it, or a review that runs nothing taking its shell away.
# Only these tools, and only those the list had. An empty list means no
# tools; it never means the defaults.
READ_ONLY_TOOLS: Final[tuple[str, ...]] = ("Read", "Glob", "Grep")


def default_tools_for_agent(agent_type: str) -> list[str]:
    """The tools an agent type runs with when nothing names its tools."""
    return list(_AGENT_TOOLS.get(agent_type, _AGENT_TOOLS["developer"]))


def parse_tool_list(value: Any) -> list[str] | None:
    """A tool list as stored or sent: None when unset, else a list of names.

    A JSON string (a JSONB value read as text) is parsed. Anything else that
    is not a list, or a string that is not a JSON list, is an empty list:
    a malformed list grants nothing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list | tuple):
        return []
    return [str(tool) for tool in value]


def tools_for_run(agent_type: str, tools: list[str] | None) -> list[str]:
    """The tools a run gets: the type's defaults when ``tools`` is None, else ``tools``, an empty list included."""
    return default_tools_for_agent(agent_type) if tools is None else list(tools)


def read_only_if_emptied(before: list[str], after: list[str]) -> list[str]:
    """``after``, or the read-only tools ``before`` had when ``after`` took every one.

    The fallback never adds a tool: a list with no read-only tool falls back
    to none, which is no tools.
    """
    if after:
        return list(after)
    return [tool for tool in before if tool in READ_ONLY_TOOLS]


def default_policy_for_agent(agent_type: str) -> SafetyPolicy:
    """Baseline safety policy for a given agent type.

    Encodes today's production behavior:
    - Ring 2 deny: host-sensitive paths (credentials, /etc, /root, etc.).
    - Ring 3 content checks: every pattern in DANGEROUS_BASH_PATTERNS.
    - Ring 3 command checks: a Docker client run, a file named .env read
      (embry0.safety.command_checks).

    There are no allow rules: the SDK executor runs the CLI in
    bypassPermissions mode, where everything a deny rule does not refuse is
    allowed, and the CLI ignores a project's allow rules in a workspace nobody
    marked trusted anyway.

    Callers may layer additional rules on top (e.g., per-repo or per-pipeline
    overrides); this factory is the irreducible baseline.
    """
    from embry0.safety.patterns import DANGEROUS_BASH_PATTERNS

    content_checks = [
        ContentRule(pattern=p, tools=["Bash"], reason=f"dangerous bash pattern: {p}") for p in DANGEROUS_BASH_PATTERNS
    ]
    command_checks = [
        CommandCheck(runs=DANGEROUS_COMMANDS, reason="a container client or daemon, which can reach the host"),
        CommandCheck(reads=SECRET_FILES, reason="a file of secrets"),
    ]
    return SafetyPolicy(
        allowed_tools=default_tools_for_agent(agent_type),
        deny_rules=list(_BASELINE_DENY_RULES),
        content_checks=content_checks,
        network_egress_allowlist=[],
        command_checks=command_checks,
    )


# ---- Ring-2 path enforcement as a Python predicate (EMB-45) ----
#
# Claude Code enforces deny_rules before dispatching a tool. embry0 applies the
# same rules itself with evaluate_ring2_paths(): the direct-API executors have
# no CLI, and the SDK executor's PreToolUse hook applies them as a second layer
# that nothing in the sandbox can rewrite. It reproduces the deny-glob
# semantics as a pure predicate, for the file tools and for the paths a Bash
# command names (embry0.safety.bash_paths).

# tool_input keys that carry a filesystem path, per tool. Grep's pattern is a
# regular expression, never a path.
_PATH_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "Read": ("file_path", "path"),
    "Write": ("file_path", "path"),
    "Edit": ("file_path", "path"),
    "NotebookEdit": ("notebook_path",),
    "Glob": ("path", "pattern"),
    "Grep": ("path",),
}

_RULE_RE: Final[re.Pattern[str]] = re.compile(r"^(\w+)\((.*)\)$")

# The tool calls a path rule refuses, as Claude Code applies it: a Read(path)
# rule covers every file tool, an Edit(path) rule the tools that write files.
# Claude Code ignores the path in a rule for any other tool (Write, Glob,
# Grep); this predicate still applies such a rule to its own tool, so it never
# refuses less than the CLI.
_RULE_COVERS: Final[dict[str, frozenset[str]]] = {
    "Read": frozenset({"Read", "Glob", "Grep", "Edit", "Write", "NotebookEdit"}),
    "Edit": frozenset({"Edit", "Write", "NotebookEdit"}),
}


def rule_path_to_absolute(rule_path: str, *, cwd: str, home: str) -> str:
    """The absolute path or glob a permission-rule path names, read as Claude Code reads it.

    ``//etc/**`` is ``/etc/**``; ``~/.ssh/**`` is under ``home``; ``/src/**`` is
    under the project root, which is ``cwd``: the directory the agent runs in,
    whose ``.claude/settings.json`` holds the rules. A single leading ``/`` is
    NOT the filesystem root (#508).

    Raises ValueError for an unanchored path such as ``*.pem`` or ``src/**``.
    Claude Code matches those gitignore-style, at any depth, which this module
    does not reproduce; evaluate_ring2_paths fails closed on them rather than
    refuse less than the CLI.
    """
    if rule_path.startswith("//"):
        return rule_path[1:]
    if rule_path.startswith("~/"):
        return home.rstrip("/") + rule_path[1:]
    if rule_path.startswith("/"):
        return cwd.rstrip("/") + rule_path
    raise ValueError(f"permission-rule path {rule_path!r} is not anchored with //, ~/ or /")


def _glob_to_regex(glob: str) -> str:
    """Translate a Claude-Code permission glob to an anchored regex.

    ``**`` matches across path separators; ``*`` matches within a segment. A
    trailing ``/**`` also matches the directory itself (so ``/etc/**`` denies
    both ``/etc`` and ``/etc/passwd``).
    """
    if glob.endswith("/**"):
        base = glob[: -len("/**")]
        return f"{_glob_to_regex(base)}(?:/.*)?"
    out: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "/":
            out.append("/")
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _expand_tilde(raw: str, home: str) -> str:
    """``~`` and ``~/…`` are under ``home``; ``~user`` under that user's home, or ValueError."""
    if not raw.startswith("~"):
        return raw
    user, sep, rest = raw[1:].partition("/")
    if not user:
        base = home
    else:
        try:
            base = pwd.getpwnam(user).pw_dir
        except KeyError:
            raise ValueError(f"cannot resolve ~{user}: no such user") from None
    return base + (sep + rest if sep else "")


def _absolute(raw: str, *, cwd: str, home: str) -> str:
    """A tool's path argument as an absolute, ~-expanded, lexically-normalized path."""
    expanded = _expand_tilde(raw, home)
    if not expanded.startswith("/"):
        expanded = os.path.join(cwd, expanded)
    # Lexical normalization collapses ".." so /workspace/../etc/passwd
    # resolves to /etc/passwd and is caught by the /etc deny glob.
    path = os.path.normpath(expanded)
    # normpath keeps exactly two leading slashes (POSIX leaves their meaning
    # to the system); Linux reads //etc/passwd as /etc/passwd.
    if path.startswith("//"):
        path = "/" + path.lstrip("/")
    return path


class _TooLong(Exception):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.length = length


def _candidate_paths(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str,
    home: str,
) -> list[tuple[PathRef, str]]:
    """The paths a tool call touches, each with the tool whose rules apply to it.

    A file tool's paths are checked as that tool. A shell command's paths
    (Bash, Monitor) are checked as Read when it reads them and as Write when it
    writes them, so a Read rule refuses both and an Edit rule only the writes.
    """
    if tool_name in SHELL_TOOLS:
        command = tool_input.get("command")
        if not isinstance(command, str) or not command:
            return []
        if len(command) > MAX_CHECKED_INPUT:
            raise _TooLong(len(command))
        found = _parsed(command, cwd, home)
        if isinstance(found, BashSyntaxError):
            raise BashSyntaxError(*found.args)
        return [(p, "Read") for p in found.reads] + [(p, "Write") for p in found.writes]
    paths: list[tuple[PathRef, str]] = []
    for key in _PATH_KEYS.get(tool_name, ()):
        raw = tool_input.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        # Glob's pattern is only a filesystem target when absolute or ~-rooted;
        # a bare glob like "**/*.py" is relative to the search root.
        if key == "pattern" and not (raw.startswith("/") or raw.startswith("~")):
            continue
        # A search's root is checked, not the tree under it: the CLI and the
        # direct executors leave the denied files out of a search's results.
        paths.append((PathRef.exact(_absolute(raw, cwd=cwd, home=home)), tool_name))
    return paths


def evaluate_ring2_paths(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str = "/workspace",
    home: str | None = None,
    project_root: str | None = None,
) -> Verdict:
    """Deny a tool call whose path matches a Ring-2 deny glob.

    Relative paths resolve against ``cwd``, the directory the call runs in; a
    rule's ``/path`` is under ``project_root`` (``cwd`` when not given).

    Reproduces Claude Code's deny-rule enforcement: rule paths are anchored by
    :func:`rule_path_to_absolute`, and a rule refuses the tools in
    ``_RULE_COVERS``. A shell command (Bash, Monitor) is refused when a path
    it names, as :func:`embry0.safety.bash_paths.bash_paths` reads it, matches
    a rule that covers reading it (or writing it, for a path it writes); a
    glob or a value only known at run time is refused when the paths it can
    name reach a rule. A command that cannot be read, and any internal error,
    deny the call.
    """
    try:
        if home is None:
            home = os.path.expanduser("~")
        candidates = _candidate_paths(tool_name, tool_input, cwd=cwd, home=home)
        if not candidates:
            return Verdict.allow()
        for rule in policy.deny_rules:
            m = _RULE_RE.match(rule)
            if not m:
                continue
            rule_tool, rule_path = m.group(1), m.group(2)
            covers = _RULE_COVERS.get(rule_tool, frozenset({rule_tool}))
            applicable = [ref for ref, as_tool in candidates if as_tool in covers]
            if not applicable:
                continue
            rule_glob = rule_path_to_absolute(rule_path, cwd=project_root or cwd, home=home)
            # DOTALL: a deny glob matches a path with a newline in any segment.
            # The optional final newline keeps what `$` matched before, so a
            # path that ends in a newline is denied as it was.
            pattern = re.compile(_glob_to_regex(rule_glob) + "\n?", re.DOTALL)
            for ref in applicable:
                if not ref.is_exact and could_match(ref, rule_glob):
                    return Verdict.deny(
                        f"Blocked by safety policy: {tool_name} names {ref.path!r}, a glob, a directory that "
                        f"is not known or a value only known at run time, which can reach deny rule {rule!r}"
                    )
                if ref.is_exact and pattern.fullmatch(ref.path):
                    return Verdict.deny(
                        f"Blocked by safety policy: {tool_name} path {ref.path!r} matches deny rule {rule!r}"
                    )
        return Verdict.allow()
    except _TooLong as exc:
        return Verdict.deny(_too_long(tool_name, exc.length))
    except BashSyntaxError as exc:
        return Verdict.deny(f"Blocked by safety policy: the {tool_name} command cannot be checked: {exc}")
    except Exception as exc:
        return Verdict.deny(f"ring-2 path check failed: {exc!r}")


def gate_tool_call(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str = "/workspace",
    home: str | None = None,
    skills: SkillSnapshot | None = None,
    project_root: str | None = None,
) -> Verdict:
    """The full pre-dispatch gate: name + Ring-3 content + Ring-2 paths, and a skill's shell commands.

    The direct-API executors call it before every tool dispatch, and the SDK
    executor's PreToolUse hook calls it for every tool call the CLI makes.

    Runs :func:`evaluate_policy` (tool-name allowlist + content checks) then
    :func:`evaluate_ring2_paths` (filesystem deny globs). A ``Skill`` (or
    ``SlashCommand``) call is then checked by :func:`gate_skill_call` against
    ``skills``, the skills the session loaded; without them it is refused.
    ``cwd`` is the directory the call runs in, ``project_root`` the workspace
    (``cwd`` when not given): a rule anchored at the project root is under it.
    First denial wins; all are fail-closed.
    """
    verdict = evaluate_policy(policy, tool_name, tool_input, cwd=cwd, home=home)
    if not verdict.allowed:
        return verdict
    verdict = evaluate_ring2_paths(policy, tool_name, tool_input, cwd=cwd, home=home, project_root=project_root)
    if not verdict.allowed or tool_name not in SKILL_TOOLS:
        return verdict
    return gate_skill_call(policy, tool_name, tool_input, cwd=cwd, home=home, skills=skills, project_root=project_root)


def gate_skill_call(
    policy: SafetyPolicy,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    cwd: str,
    home: str | None,
    skills: SkillSnapshot | None,
    project_root: str | None = None,
) -> Verdict:
    """Refuse a skill whose inline shell commands the agent could not run as Bash calls.

    The CLI runs a skill's ``!`…``` and ```` ```! ```` commands itself, and no
    tool call reaches the hook for them. So every text the CLI may run for the
    skill (as the session loaded it, and as its file is now:
    embry0.safety.skill_commands) is read here, and every command in it, with
    the call's arguments filled in, must pass the checks a Bash call gets in
    ``cwd``, the session's directory, where the CLI runs them. An agent whose
    allowlist has no Bash may not run a skill that runs commands. A skill the
    session did not load from the workspace or a mounted source, or that
    cannot be read or parsed, is refused.
    """
    try:
        call = skill_call(tool_name, tool_input)
        if skills is None:
            raise SkillError(f"skill {call.name!r} cannot be checked: this executor reads no skills")
        commands: list[str] = []
        for text in skills.texts(call.name):
            commands.extend(c for c in skill_commands(text, call.arguments) if c not in commands)
        if not commands:
            return Verdict.allow()
        # An empty allowlist names no tool, so it runs no Bash either.
        if policy.allowed_tools is not None and "Bash" not in policy.allowed_tools:
            return Verdict.deny(
                f"Blocked by safety policy: skill {call.name!r} runs shell commands, and this agent may not run any"
            )
        for command in commands:
            verdict = gate_tool_call(
                policy, "Bash", {"command": command}, cwd=cwd, home=home, project_root=project_root
            )
            if not verdict.allowed:
                why = verdict.reason.removeprefix("Blocked by safety policy: ")
                return Verdict.deny(f"Blocked by safety policy: skill {call.name!r} runs {command!r}: {why}")
        return Verdict.allow()
    except SkillError as exc:
        return Verdict.deny(f"Blocked by safety policy: {exc}")
    except Exception as exc:
        return Verdict.deny(f"skill check failed: {exc!r}")


def render_settings_json(policy: SafetyPolicy) -> dict[str, Any]:
    """Serialize a SafetyPolicy into a Claude Code settings.json dict.

    The executor writes it to /workspace/.claude/settings.json before invoking
    Claude, so it replaces whatever settings file the repository carries. It
    holds only the deny rules, and is not where they are held alone: the
    workspace is writable, so the same rules also reach the CLI as
    --disallowedTools (build_sdk_options) and the PreToolUse hook
    (gate_tool_call), neither of which the sandbox can change.
    """
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "deny": list(policy.deny_rules),
        },
    }

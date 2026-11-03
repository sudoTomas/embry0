"""The context auditor's command: the files it reads and the report it writes (#170).

The context-auditor agent's system prompt (``CONTEXT_AUDITOR_SYSTEM_PROMPT``)
says how to audit. What embry0's code fixes reaches the agent in the job's
task instead, written when the task is built: the command that lists the
instruction files, from the :class:`~embry0.services.instruction_files.FileRules`
the repository reads then (the settings document's ``context_audit`` section,
its organization's layer and its own over the deployment's), and the
report's shape, which :func:`embry0.services.context_audit.report_counts`,
:func:`embry0.services.proposals.parse_findings` and
:mod:`embry0.workflows.issue_to_pr.audit_delivery` read back.

A builtin agent's prompt reaches a deployment's stored row only when the row
is created, and the stored row wins. While the list and the shape lived in
the prompt, a deployment that had already run the agent kept the command of
the release that first stored its row: the schedule fingerprinted the
current list while the agent ran the old one. In the task, each job carries
the command of the release that built it, and the section says that where
the system prompt gives another command or another shape, the task wins, so
no agent reset is needed.

A context-audit task built anywhere else, such as one created with
``POST /jobs``, gets the same section when the agent runs
(:func:`with_command`, called by the generic agent node, with the rules the
job read for its repository when it started).

Every function here takes the rules it writes the command from: none reads
a list of its own, so no caller can hand an agent the built-in list while
its repository's settings name another.
"""

from __future__ import annotations

from embry0.services.instruction_files import FileRules
from embry0.workflows.issue_to_pr.specialist_agents import REPORT_TARGET_CHARS

#: The line the section opens with. A task holding it carries a command already.
HEADING = "## How to run this audit"

#: The most findings the table holds; the rest are counted under it.
MAX_AUDIT_FINDING_ROWS = 40


def list_command(rules: FileRules) -> str:
    """The ``git ls-files`` command that lists the instruction files ``rules`` select.

    Each glob becomes a git pathspec with glob magic, which matches ``**/``
    and ``*`` the way :meth:`~embry0.services.instruction_files.FileRules.select`
    does for a glob the settings section accepts, and each skipped directory
    an exclude at any depth.
    """
    return "git ls-files -- " + " ".join(
        [f"':(glob){glob}'" for glob in rules.globs]
        + [f"':(exclude,glob)**/{directory}/**'" for directory in sorted(rules.skipped_dirs)]
    )


def _skipped_dir_names(rules: FileRules) -> str:
    return ", ".join(f"`{directory}`" for directory in sorted(rules.skipped_dirs))


def command(rules: FileRules) -> str:
    """The section a context-audit task ends with: the files ``rules`` select, the severities and the report's shape."""
    skipped = _skipped_dir_names(rules)
    outside = f", outside vendored and generated trees ({skipped})" if skipped else ""
    return f"""{HEADING}

embry0 writes this section from its own code, so it is current. Where \
your system prompt gives another file list, another command, other \
severities or another report shape, follow this section.

### The instruction files

The instruction files are every tracked file that matches one of the globs \
in this command{outside}. In a glob, `**/` matches any number of \
directories, none included, and `*` stays inside one path segment. The \
command lists exactly those files; run it as written:

```
{list_command(rules)}
```

### Severity

- `misleads`: an agent following the line would do the wrong thing or \
state something false.
- `dead`: the line points at something that does not exist.
- `drift`: an outdated detail with little consequence.

Unverifiable and unreachable lines are listed in their own sections, not \
counted as stale.

### The report

Programs read the counts from the start of your report, so that start is \
fixed. The first line is `Stale: N` and nothing else, where N is the number \
of `misleads` and `dead` findings, the ones the table leaves out included. \
The second line is `Files: M` and nothing else, where M is the number of \
instruction files you read (READMEs read as context do not count). Next \
comes a `## Findings` heading and one table with exactly these columns:

| Severity | Location | Claim | Evidence | Fix |
|---|---|---|---|---|

One row per finding, `misleads` first, then `dead`, then `drift`, at most \
{MAX_AUDIT_FINDING_ROWS} rows. Location is `path:line`; Claim is the line, quoted \
and short; Evidence is what contradicts it (a file:line, or the command you \
ran and what it printed); Fix is the corrected text, `delete`, or the \
question. Under the table, say how many findings were left out, if any, \
and give one line of coverage: the references the script checked, how many \
did not resolve, and how many of those you dropped, by reason. With nothing \
to report, write `No findings.` under the heading and no table.

Then, in this order:
- `## Pull` - the diff in a diff code block, then the `git apply --check` \
command and its result; with no certain fix, say so.
- `## Unverifiable` - each claim nothing in the repository can confirm, as \
path:line and the claim.
- `## Unreachable from a clone` - each reference an agent in a clone cannot \
reach, as path:line and the reference.
- `## Not covered` - what this audit leaves out: instruction files that \
live outside the repository (a user's own files, a home-directory skill), \
claims that need a running service or a network the sandbox lacks, and \
references the script cannot extract.
- `## Assumptions` - the assumptions you made, as the deliverable contract \
asks.

The whole report, the diff included, stays under {REPORT_TARGET_CHARS:,} \
characters. When it does not fit, cut in this order until it does: the \
`drift` rows and their hunks, then the entries under the last four \
headings, then the `dead` hunks. Never cut the first two lines or a \
`misleads` row, and say under the table what you cut.

With no instruction files, the report is `Stale: 0`, then `Files: 0`, then \
where you looked: the command you ran and what it printed."""


def carries_command(task: str) -> bool:
    """Whether ``task`` holds the section already: a line that is :data:`HEADING` and nothing else."""
    return any(line.strip() == HEADING for line in task.splitlines())


def with_command(task: str, *, rules: FileRules) -> str:
    """``task`` ending with :func:`command` for ``rules``; ``task`` unchanged when it carries one already.

    The task's own lines come first and stay as they are: its first line is
    the job's title, and the branch an audit pushes is named from its start.
    """
    if carries_command(task):
        return task
    section = command(rules)
    return f"{task.rstrip()}\n\n{section}" if task.strip() else section

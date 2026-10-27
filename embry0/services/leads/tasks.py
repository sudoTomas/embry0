"""What a lead's job is asked to do (leads, spec 2026-10-25).

A backlog ticket runs as the ticket it is, through its tracker's own path. The
other kinds become work no person wrote up, so the task says it here: what
was seen, where, and what done looks like, with the rule a quick fix would
break (never silence the check, the alert or the flake).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _area_line(row: Mapping[str, Any]) -> list[str]:
    return [f"It is in the {row['area']} area of the repository.", ""] if row.get("area") else []


def lead_task(row: Mapping[str, Any]) -> str:
    """The job's task for a stored lead of kind ci, flaky, alert or review."""
    kind, repo = str(row["kind"]), str(row["repo"])
    data = dict(row.get("data") or {})
    details = str(row.get("details") or "").strip()
    url = row.get("url")
    if kind == "ci":
        lines = [
            f"Fix the check {data.get('check')} failing on {data.get('branch')} of {repo}.",
            "",
            *_area_line(row),
            f"It fails on commit {data.get('sha')}" + (f" ({url})." if url else "."),
            details,
            "",
            "Find why it fails: read the check's log through the CI the repository uses, or reproduce it locally.",
            "Fix the cause in the code or in the test, and say in the pull request what was wrong.",
            "Do not disable, skip or loosen the check.",
        ]
    elif kind == "flaky":
        shas = ", ".join(str(s)[:7] for s in data.get("shas") or [])
        lines = [
            f"Make the check {data.get('check')} on {repo} reliable.",
            "",
            *_area_line(row),
            f"It failed and then passed on the same commit of {data.get('branch')}: {shas}.",
            "",
            "Find what makes it nondeterministic (timing, ordering, shared state, the network, a clock),",
            "fix that cause, and say in the pull request how you know it is fixed.",
            "Do not add retries or sleeps that hide it.",
        ]
    elif kind == "alert" and data.get("source") == "dependabot":
        fixed = data.get("fixed")
        manifest = data.get("manifest") or "its manifest"
        lines = [
            f"Fix a dependency alert in {repo}: {row.get('title')}.",
            "",
            *_area_line(row),
            details,
            "",
            (
                f"Upgrade {data.get('package')} to {fixed} or later in {manifest}, update the lockfile,"
                if fixed
                else f"No fixed version of {data.get('package')} exists yet: remove it or replace it,"
            ),
            "run the tests that cover it, and say in the pull request what changed and anything that broke.",
        ]
    elif kind == "alert":
        lines = [
            f"Fix a code-scanning alert in {repo}: {row.get('title')}.",
            "",
            *_area_line(row),
            details,
            "",
            "Change the code so the rule no longer finds the problem. Do not suppress or dismiss the alert.",
        ]
    elif kind == "review":
        head = data.get("head_ref")
        lines = [
            f"Answer the open review threads on {repo}#{data.get('number')}" + (f" ({url})." if url else "."),
            "",
            *_area_line(row),
            f"The workspace has the pull request's branch {head} checked out.",
            "",
            "The threads nobody answered:",
            details,
            "",
            "For each thread, make the change the reviewer asked for; where you disagree, say why in the commit",
            f"message. Commit and push to {head}. The pull request exists: do NOT create a new pull request or branch.",
        ]
    elif kind == "crack":
        lines = [
            f"Mend a crack the scout found in {repo}: {row.get('title')}",
            "",
            *_area_line(row),
            details,
            "",
            "First check the evidence still holds on the default branch; if it does not, say so and stop.",
            "Fix the cause, prove it with a test or the command that showed it, and say in the pull request",
            "what was wrong. Where the seal says a person must decide first, ask instead of choosing.",
        ]
    elif kind == "weed" and data.get("source") == "map":
        from embry0.services.knowledge_maps import removal_task

        return removal_task(row)
    elif kind == "weed":
        pull = str(data.get("pull") or "").strip()
        lines = [
            f"Pull a weed the scout found in {repo}: {str(row.get('title') or '').removeprefix('Weed: ')}",
            "",
            *_area_line(row),
            details,
            "",
            *(["The diff the scout checked:", "```diff", pull, "```", ""] if pull else []),
            "Confirm nothing references it on the default branch, then remove it (the diff above, or its",
            "equivalent if the code moved), run the check that covers it, and open a pull request.",
            "Change no behavior: if anything a user would notice changes, it is not a weed; stop and say so.",
        ]
    else:
        raise ValueError(f"a {kind} lead has no task of its own")
    return "\n".join(line for line in lines if line is not None).strip()

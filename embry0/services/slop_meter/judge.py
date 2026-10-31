"""Asking a model whether an outlier commit is slop (spec 2026-10-26, slop-o-meter).

The few commits that add thousands of weighted lines at once are judged one by
one, on ``background_models.slop_judge`` through the background call every
brief makes (``embry0.services.background_models``): slop, or not slop
with a reason (a code drop, a restructuring, a vendored import). A judgment is
kept per commit and never asked again.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from embry0.services.background_models import BackgroundCalls, Endpoint
from embry0.services.slop_meter.score import Outlier

#: The key of ``background_models`` the judge runs on, and what its spend is recorded under.
FEATURE = "slop_judge"

SYSTEM_PROMPT = """\
You judge one large commit of a software repository: did it add bulk code no
person wrote or reviewed line by line (slop, such as a large AI-generated
change), or is its size explained by something else (not slop: the project's
initial import, a code drop moved in from elsewhere, a restructuring that moved
files, a vendored or generated library, a data or fixture dump)?
Judge from what the message and the files say. When it is unclear, answer
slop: false only if the files themselves look moved, vendored or generated.
Answer with JSON: {"slop": true|false, "reason": "<one plain sentence>"}."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"slop": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 300}},
    "required": ["slop", "reason"],
    "additionalProperties": False,
}

_FILES_SHOWN = 40


def user_prompt(repo: str, outlier: Outlier) -> str:
    c = outlier.commit
    files = sorted(c.files, key=lambda f: -f[1])[:_FILES_SHOWN]
    listing = "\n".join(f"- {path} (+{a} −{d})" for path, a, d in files)
    more = len(c.files) - len(files)
    return (
        f"Repository: {repo}\n"
        f"Commit {c.sha[:12]}, {c.at:%Y-%m-%d}, {outlier.age_days} days into the project.\n"
        f"Net weighted lines added: {round(outlier.net):,}. The codebase then: about {round(outlier.size_at):,} weighted lines.\n"
        f"Files ({len(c.files)}):\n{listing}" + (f"\n…and {more} more" if more > 0 else "") + "\n\n"
        f"Message:\n{c.message[:1500]}"
    )


def parse_judgment(text: str) -> tuple[bool, str]:
    """The model's answer as ``(slop, reason)``; ValueError when it is not one."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict) or not isinstance(data.get("slop"), bool):
        raise ValueError("the answer has no boolean slop")
    return data["slop"], str(data.get("reason") or "").strip()[:300]


async def judge(
    repo: str, outlier: Outlier, endpoint: Endpoint, client: httpx.AsyncClient, calls: BackgroundCalls
) -> tuple[bool, str]:
    """``(slop, reason)``; ``ValueError`` for an answer that is not one, ``httpx.HTTPError`` for a failed call,
    ``BackgroundCallRefused`` when the budget caps refuse it."""
    answer = await calls.ask(
        FEATURE,
        endpoint,
        client,
        repo=repo,
        system=SYSTEM_PROMPT,
        user=user_prompt(repo, outlier),
        schema=SCHEMA,
        name="judgment",
    )
    return parse_judgment(answer)

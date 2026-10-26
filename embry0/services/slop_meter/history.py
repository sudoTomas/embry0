"""A repository's history as the Slop-O-Meter reads it (spec 2026-10-26, slop-o-meter).

``git log`` output in one format (:data:`LOG_FORMAT`) becomes :class:`Commit`
rows: who wrote each commit and when, its per-file line counts, its human
co-authors, whether an AI co-wrote it, whether a bot made it, and how many
sub-commits a squash merge folded in. After Slop-O-Meter's published slop score
algorithm (https://github.com/slop-o-meter/slop-o-meter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: One record per commit: a record separator, then the fields, then ``--numstat`` lines.
LOG_FORMAT = "%x1e%H%x1f%aI%x1f%aN%x1f%aE%x1f%B%x1f"
#: The ``git log`` arguments that go with it: per-file counts, renames as delete and add, raw paths.
LOG_ARGS = ("-c", "core.quotepath=off", "log", "--no-renames", "--numstat", f"--format={LOG_FORMAT}")

KNOWN_BOTS = frozenset(
    {
        "dependabot[bot]",
        "dependabot-preview[bot]",
        "renovate[bot]",
        "renovate-bot",
        "github-actions[bot]",
        "devin[bot]",
        "devin-ai-integration[bot]",
        "copilot[bot]",
        "copilot-swe-agent[bot]",
        "cursor[bot]",
        "claude[bot]",
        "codex[bot]",
        "sweep-ai[bot]",
    }
)
_AI_EMAIL_PREFIXES = (
    "codex@",
    "claude@",
    "cursoragent@",
    "devin@",
    "copilot@",
    "aider@",
    "gemini@",
    "jules@",
    "openhands@",
)
_NOREPLY_LOGIN = re.compile(r"\A(?:\d+\+)?([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))@users\.noreply\.github\.com\Z", re.I)
_CO_AUTHOR = re.compile(r"^co-authored-by:[ \t]*(.*?)[ \t]*<([^>\n]+)>[ \t]*$", re.I | re.M)
_BULLET = re.compile(r"^[ \t]*[*-][ \t]+\S", re.M)


def is_bot(name: str, email: str) -> bool:
    """A bot or an AI coding agent: a known name, a ``[bot]`` name or address, ``noreply@``, or an agent's address."""
    name, email = name.strip().lower(), email.strip().lower()
    if name in KNOWN_BOTS or name.endswith("[bot]"):
        return True
    return email.endswith("[bot]@users.noreply.github.com") or email.startswith(("noreply@", *_AI_EMAIL_PREFIXES))


def login_of_email(email: str) -> str | None:
    """The forge login a ``{id}+{login}@users.noreply.github.com`` address carries, lower-cased."""
    found = _NOREPLY_LOGIN.match(email.strip())
    return found.group(1).lower() if found else None


@dataclass(frozen=True)
class Commit:
    sha: str
    at: datetime
    author_name: str
    author_email: str
    message: str
    #: (path, lines added, lines deleted); a binary file counts 0 and 0.
    files: tuple[tuple[str, int, int], ...] = ()
    #: Human co-authors, (name, email), in trailer order.
    coauthors: tuple[tuple[str, str], ...] = ()
    ai_coauthor: bool = False
    bot: bool = False
    #: A squash merge's sub-commits (its body's bullet lines); 0 for any other commit.
    subcommits: int = 0
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def author(self) -> str:
        """The contributor key: the author's address, lower-cased."""
        return self.author_email.strip().lower()

    def to_row(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "authored_at": self.at,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "message": self.message[:4000],
            "files": [list(f) for f in self.files],
            "coauthors": [list(c) for c in self.coauthors],
            "ai_coauthor": self.ai_coauthor,
            "bot": self.bot,
            "subcommits": self.subcommits,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Commit:
        at = row["authored_at"]
        return cls(
            sha=str(row["sha"]),
            at=at if at.tzinfo else at.replace(tzinfo=UTC),
            author_name=str(row.get("author_name") or ""),
            author_email=str(row.get("author_email") or ""),
            message=str(row.get("message") or ""),
            files=tuple((str(p), int(a), int(d)) for p, a, d in row.get("files") or []),
            coauthors=tuple((str(n), str(e)) for n, e in row.get("coauthors") or []),
            ai_coauthor=bool(row.get("ai_coauthor")),
            bot=bool(row.get("bot")),
            subcommits=int(row.get("subcommits") or 0),
        )


def _count(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _path(raw: str) -> str:
    """A numstat path; a C-quoted one (an odd character in it) loses its quotes."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].encode().decode("unicode_escape", errors="replace")
    return raw


def commit_from(sha: str, at: datetime, name: str, email: str, body: str, numstat: str) -> Commit:
    files = []
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[2]:
            files.append((_path(parts[2]), _count(parts[0]), _count(parts[1])))
    humans: list[tuple[str, str]] = []
    ai = False
    for co_name, co_email in _CO_AUTHOR.findall(body):
        if is_bot(co_name, co_email):
            ai = True
        elif co_email.strip().lower() != email.strip().lower() and (co_name, co_email) not in humans:
            humans.append((co_name.strip(), co_email.strip()))
    bullets = len(_BULLET.findall(body))
    return Commit(
        sha=sha,
        at=at.astimezone(UTC),
        author_name=name,
        author_email=email,
        message=body.strip(),
        files=tuple(files),
        coauthors=tuple(humans),
        ai_coauthor=ai,
        bot=is_bot(name, email),
        subcommits=bullets if bullets >= 2 else 0,
    )


def parse_log(text: str) -> list[Commit]:
    """``git log`` output in :data:`LOG_FORMAT` with ``--numstat``, as commits in the order it lists them."""
    commits = []
    for record in text.split("\x1e"):
        if not record.strip():
            continue
        fields = record.split("\x1f")
        if len(fields) < 6:
            continue
        sha, date, name, email, body, numstat = fields[0].strip(), fields[1], fields[2], fields[3], fields[4], fields[5]
        try:
            at = datetime.fromisoformat(date.strip())
        except ValueError:
            continue
        commits.append(commit_from(sha, at, name, email, body, numstat))
    return commits

"""Knowledge maps (spec 2026-10-27): a scanner's map of a codebase or an organization, marked and sent by people.

A scanner outside embry0 reads a codebase (or every system of an
organization) and uploads what it found as one ``embry0.map/1`` document:
the parts (modules, files, tables, services, ...) laid out on a plane, the
links between them, and a detail record per part (what it does and why it
exists, its bloat score and the reasons, how much it is used, its history).
embry0 keeps the last upload per map and shows it on the Maps page.

Each map belongs to an owner, the forge organization or account it
describes: the one the upload names, else the owner of its repository. Two
owners may each have a map of one slug. A map with neither belongs to no
one, and only an admin sees or changes it until an admin gives it an owner.

People mark parts: bloat, keep or not sure. A bloat mark on a part that
lives in a repository becomes a ``weed`` lead, offered to the repository's
owners like every lead; sending the part accepts it, and the agent removes
the part in a pull request without arguing for keeping it, because the mark
is the decision. A part can also be sent for a fix or a read-only analysis,
which start a job directly. This module is the pure part: reading an
upload, building the lead and the tasks.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.services.leads import Lead

FORMAT = "embry0.map/1"
#: Applied with ``fullmatch``: ``match`` would let ``$`` match before a final newline, so ``shop\n`` would pass.
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
#: A forge owner (an organization, an account, a group): the owner part of a repository's ``owner/name``.
OWNER = re.compile(r"^[\w.-]{1,255}$")
#: Upper bounds on one upload: past these it is refused, not cut.
MAX_NODES = 20_000
MAX_EDGES = 100_000
#: Parts one send may carry, and the size of the task a batch job is given.
MAX_SEND = 200
MAX_BATCH_TASK = 45_000
MAX_DECODED_BYTES = 64 * 1024 * 1024
#: What a part can be sent for, and the verdicts a mark can carry.
SEND_KINDS: tuple[str, ...] = ("remove", "fix", "analysis")
VERDICTS: tuple[str, ...] = ("bloat", "keep", "unsure")
BANDS = frozenset({"keep", "look", "bloat"})
#: The lead's ``source_key`` prefix: a map's weeds are told apart from the scout's by it.
SOURCE_PREFIX = "map:"


class MapError(ValueError):
    """An upload that is not a map this version reads; the message says why, for the uploader."""


@dataclass(frozen=True)
class Mark:
    by: str
    verdict: str
    note: str
    at: datetime


@dataclass(frozen=True)
class ParsedMap:
    slug: str
    title: str
    repo: str | None
    #: The forge owner the map belongs to, lowercased; None when the upload names neither an owner nor a repository.
    #: Kept beside the graph, not in it: the stored owner is the map's (it can change), and a read adds it.
    owner: str | None
    commit: str | None
    source: str
    generated_at: str | None
    #: What the viewer draws: kinds, areas, nodes, edges (and the header fields again, for one fetch).
    graph: dict[str, Any]
    #: One record per part id, only for parts the map has.
    details: dict[str, Any]


def _text(value: Any, limit: int) -> str | None:
    if value is None or value == "":
        return None
    return str(value)[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 1)


def _repo(value: Any) -> str | None:
    return value if isinstance(value, str) and _REPO.fullmatch(value) else None


def repo_owner(repo: str | None) -> str | None:
    """The owner part of an ``owner/name`` repository, lowercased (forge owners ignore case); None without one."""
    return repo.split("/", 1)[0].lower() if repo and "/" in repo else None


def _storable(value: Any) -> str | None:
    """Why ``value`` cannot be stored as JSON that Postgres reads, or None when it can.

    Python's JSON reads ``NaN`` and ``Infinity`` (and ``1e400`` as infinity), a NUL in a string and a lone
    UTF-16 surrogate; JSONB refuses all of them, so a map carrying one could not be read back as JSONB.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                return "a string holds a NUL character"
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return "a string holds a lone UTF-16 surrogate"
        elif isinstance(item, float):
            if not math.isfinite(item):
                return "a number is NaN or infinite"
        elif isinstance(item, Mapping):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)
    return None


def foreign_repos(nodes: Iterable[Mapping[str, Any]], owner: str) -> list[str]:
    """The repositories parts name that are not under ``owner``: a map holds only its owner's repositories."""
    return sorted({str(n["repo"]) for n in nodes if n.get("repo") and repo_owner(str(n["repo"])) != owner})


def clean_owner(value: Any) -> str | None:
    """An owner as stored, lowercased; MapError when it is not a forge owner's name. None and "" are no owner."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not OWNER.fullmatch(value):
        raise MapError("owner is a forge owner's name: letters, digits, '.', '_' and '-'")
    return value.lower()


def _node(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str) or not raw["id"] or len(raw["id"]) > 300:
        return None
    node: dict[str, Any] = {
        "id": raw["id"],
        "kind": _text(raw.get("kind"), 60) or "part",
        "area": _text(raw.get("area"), 120),
        "label": _text(raw.get("label"), 200) or raw["id"],
        "sub": _text(raw.get("sub"), 200),
        "path": _text(raw.get("path"), 400),
        "line": raw.get("line") if isinstance(raw.get("line"), int) and not isinstance(raw.get("line"), bool) else None,
        "url": _text(raw.get("url"), 600) if str(raw.get("url") or "").startswith("https://") else None,
        "repo": _repo(raw.get("repo")),
        "x": _number(raw.get("x")),
        "y": _number(raw.get("y")),
        "r": _number(raw.get("r")),
        "signals": [s for s in (_text(x, 300) for x in raw.get("signals") or [] if isinstance(x, str)) if s][:8],
        # How the part is removed when it is not a file (a database table: a migration), in the scanner's words.
        "removal": _text(raw.get("removal"), 600),
    }
    bloat = raw.get("bloat")
    if isinstance(bloat, Mapping) and _number(bloat.get("score")) is not None and bloat.get("band") in BANDS:
        node["bloat"] = {"score": max(0, min(100, int(bloat["score"]))), "band": bloat["band"]}
    for key in ("age", "use"):
        if isinstance(raw.get(key), Mapping):
            node[key] = {k: v for k, v in raw[key].items() if isinstance(v, str | int | float) or v is None}
    return node


def parse_map(doc: Any, slug: str) -> ParsedMap:
    """An upload as stored; MapError when it is not an ``embry0.map/1`` document for ``slug``."""
    if not SLUG.fullmatch(slug):
        raise MapError("a map's slug is lowercase letters, digits and hyphens, at most 64")
    if not isinstance(doc, Mapping):
        raise MapError("the upload is not a JSON object")
    unstorable = _storable(doc)
    if unstorable is not None:
        raise MapError(f"{unstorable}, which a map cannot store")
    if doc.get("format") != FORMAT:
        raise MapError(f"format must be {FORMAT!r}")
    if doc.get("slug") not in (None, slug):
        raise MapError("the document's slug is not the one in the path")
    raw_nodes, raw_edges = doc.get("nodes"), doc.get("edges") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise MapError("nodes and edges are lists")
    if len(raw_nodes) > MAX_NODES or len(raw_edges) > MAX_EDGES:
        raise MapError(f"a map holds at most {MAX_NODES} parts and {MAX_EDGES} links")
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_nodes:
        node = _node(raw)
        if node is None:
            raise MapError("every part needs an id, a string of at most 300 characters")
        if node["id"] in seen:
            raise MapError(f"the part id {node['id']!r} is there twice")
        seen.add(node["id"])
        nodes.append(node)
    edges = [
        {"s": e["s"], "t": e["t"], "rel": _text(e.get("rel"), 40)}
        for e in raw_edges
        if isinstance(e, Mapping) and e.get("s") in seen and e.get("t") in seen
    ]
    kinds = [
        {"id": str(k["id"])[:60], "label": _text(k.get("label"), 120) or str(k["id"])[:60]}
        for k in doc.get("kinds") or []
        if isinstance(k, Mapping) and k.get("id")
    ]
    areas = [
        {
            "id": str(a["id"])[:120],
            "label": _text(a.get("label"), 120) or str(a["id"])[:120],
            "summary": _text(a.get("summary"), 600),
            "x": _number(a.get("x")),
            "y": _number(a.get("y")),
            "r": _number(a.get("r")),
        }
        for a in doc.get("areas") or []
        if isinstance(a, Mapping) and a.get("id")
    ]
    raw_details = doc.get("details") or {}
    if not isinstance(raw_details, Mapping):
        raise MapError("details is an object keyed by part id")
    details = {k: v for k, v in raw_details.items() if k in seen and isinstance(v, Mapping)}
    title = _text(doc.get("title"), 120) or slug
    repo = _repo(doc.get("repo"))
    owner = clean_owner(doc.get("owner"))
    if owner is not None and repo is not None and owner != repo_owner(repo):
        raise MapError(f"owner must be the owner of the map's repo, {repo_owner(repo)!r}")
    owner = owner or repo_owner(repo)
    commit = _text(doc.get("commit"), 64)
    source = _text(doc.get("source"), 200) or ""
    generated_at = _text(doc.get("generated_at"), 40)
    graph = {
        "slug": slug,
        "title": title,
        "repo": repo,
        "commit": commit,
        "source": source,
        "generated_at": generated_at,
        "kinds": kinds,
        "areas": areas,
        "nodes": nodes,
        "edges": edges,
    }
    return ParsedMap(
        slug=slug,
        title=title,
        repo=repo,
        owner=owner,
        commit=commit,
        source=source,
        generated_at=generated_at,
        graph=graph,
        details=details,
    )


def part_repo(map_repo: str | None, node: Mapping[str, Any]) -> str | None:
    """The repository a part lives in: its own, else the map's when the part is a file of it; None otherwise."""
    own = _repo(node.get("repo"))
    if own:
        return own
    return map_repo if map_repo and (node.get("path") or node.get("removal")) else None


def removable(map_repo: str | None, node: Mapping[str, Any]) -> bool:
    """Whether an agent can remove the part with a code change.

    A file or folder (``path``) in a repository can be, and so can a part the
    scanner says how to remove in code (``removal``: a database table the
    repository's migrations own is dropped by a new migration). Anything else
    (a whole system or repository, a host, a table nobody's code owns) is not a
    code change, so a bloat mark on it stays on the map and makes no lead, and
    it cannot be sent for removal. It can still go for a fix or an analysis
    when it lives in a repository.
    """
    return part_repo(map_repo, node) is not None and bool(node.get("path") or node.get("removal"))


def source_key(slug: str, node_id: str) -> str:
    return f"{SOURCE_PREFIX}{slug}:{node_id}"


def owner_query(owner: str | None) -> str:
    """The start of a map page's query that names its owner, ``?owner=<owner>&`` (empty for no owner)."""
    from urllib.parse import quote

    return f"?owner={quote(owner or '', safe='')}&"


def part_url(base: str, slug: str, node_id: str, *, owner: str | None) -> str:
    """The part on its map's page; ``owner=`` names the map even when another owner has one of the same slug."""
    from urllib.parse import quote

    return f"{base.rstrip('/')}/maps/{quote(slug)}{owner_query(owner)}part={quote(node_id, safe='')}"


def part_text(node: Mapping[str, Any], detail: Mapping[str, Any] | None, map_title: str) -> str:
    """Everything the map knows about a part, in plain words: what an owner reads and what an agent is given."""
    d = detail or {}
    lines = [f"Part: {node.get('label')} ({node.get('kind')}) on the {map_title} map, id `{node.get('id')}`."]
    if node.get("path"):
        lines.append(f"File: {node['path']}" + (f":{node['line']}" if node.get("line") else ""))
    if node.get("removal"):
        lines.append(f"How it is removed: {node['removal']}")
    for signal in node.get("signals") or []:
        lines.append(f"Signal: {signal}")
    bloat = d.get("bloat") or node.get("bloat")
    if isinstance(bloat, Mapping) and bloat.get("score") is not None:
        lines += ["", f"Bloat score: {bloat['score']}/100 ({bloat.get('band')})."]
        lines += [f"- +{r.get('w')} {r.get('text')}" for r in bloat.get("against") or [] if isinstance(r, Mapping)]
        lines += [f"- -{r.get('w')} {r.get('text')}" for r in bloat.get("for") or [] if isinstance(r, Mapping)]
        risks = [str(r) for r in bloat.get("risks") or [] if r]
        if risks:
            lines += ["Needs a person (not bloat):", *[f"- {r}" for r in risks]]
    narrative = d.get("narrative")
    if isinstance(narrative, Mapping):
        for key, label in (
            ("does", "What it does"),
            ("why", "Why it exists"),
            ("useful", "Case for keeping"),
            ("against", "Case against"),
        ):
            if narrative.get(key):
                lines += ["", f"{label}: {narrative[key]}"]
        if narrative.get("lean"):
            lines.append(f"The scanner's reading: {narrative['lean']} ({narrative.get('sure') or '?'} confidence).")
    use = [u for u in d.get("use") or [] if isinstance(u, Mapping)]
    if use:
        lines += ["", "Use:"]
        for u in use[:8]:
            value = f"{u['n']} {u.get('unit') or ''}".strip() if u.get("n") is not None else str(u.get("text") or "")
            lines.append(f"- {u.get('label')}: {value}" + (f" ({u['source']})" if u.get("source") else ""))
    history = d.get("history")
    if isinstance(history, Mapping) and isinstance(history.get("added"), Mapping):
        added = history["added"]
        lines += [
            "",
            f"History: added {str(added.get('date') or '')[:10]} by {added.get('author')}"
            f' ("{added.get("subject")}"); {history.get("changes") or "?"} changes.',
        ]
        prs = [p for p in history.get("prs") or [] if isinstance(p, Mapping)]
        if prs:
            lines.append("Pull requests: " + "; ".join(f"#{p.get('number')} {p.get('title')}" for p in prs[:5]))
        if history.get("tickets"):
            lines.append("Tickets: " + ", ".join(str(t) for t in history["tickets"][:8]))
    return "\n".join(lines).strip()


def map_lead(
    *,
    slug: str,
    owner: str | None,
    map_title: str,
    map_repo: str | None,
    node: Mapping[str, Any],
    detail: Mapping[str, Any] | None,
    marks: Sequence[Mark],
    url: str | None,
) -> Lead | None:
    """The weed lead a part marked bloat becomes; None when an agent cannot remove it (:func:`removable`)."""
    repo = part_repo(map_repo, node)
    if repo is None or not removable(map_repo, node):
        return None
    bloat = [m for m in marks if m.verdict == "bloat"]
    names = ", ".join(dict.fromkeys(m.by for m in bloat)) or "someone"
    notes = [m.note for m in bloat if m.note]
    label = str(node.get("label") or node.get("id"))
    summary = f"{names} marked {label} bloat on the {map_title} map" + (f": {notes[0]}" if notes else "")
    details = part_text(node, detail, map_title)
    if notes:
        details = "\n".join(f"Mark note ({m.by}): {m.note}" for m in bloat if m.note) + "\n\n" + details
    return Lead(
        kind="weed",
        repo=repo,
        source_key=source_key(slug, str(node["id"])),
        title=f"Remove {label}",
        summary=summary,
        details=details,
        action="Removes it in a pull request; the bloat mark is the decision, so the agent does not re-assess it.",
        url=url,
        severity="low",
        effort="small",
        paths=(str(node["path"]),) if node.get("path") else (),
        data={
            "source": "map",
            "map": slug,
            # Which owner's map of this slug made the lead: an upload closes only its own map's leads.
            "map_owner": owner,
            "node": str(node["id"]),
            "marked_by": list(dict.fromkeys(m.by for m in bloat)),
        },
    )


def removal_task(row: Mapping[str, Any]) -> str:
    """The task of a map's weed lead: remove the part, without re-assessing the person's decision."""
    data = dict(row.get("data") or {})
    repo = str(row["repo"])
    label = str(row.get("title") or "").removeprefix("Remove ")
    paths = [p for p in data.get("paths") or [] if p]
    lines = [
        f"Remove {label} from {repo}.",
        "",
        f"{row.get('summary') or 'A person marked this part as bloat on a knowledge map.'}",
        "The mark is the decision: do not re-assess whether to keep it.",
        "",
        *([f"It is in the {row['area']} area of the repository.", ""] if row.get("area") else []),
        *([f"Where: {', '.join(paths)}", ""] if paths else []),
        "What the map knows about it:",
        "",
        str(row.get("details") or "").strip(),
        "",
        "Delete the part and everything that only it uses, and drop every mention of it (package scripts,",
        "docs, READMEs, instruction files, config). Run the repository's tests where it has them, and open",
        "a pull request. Make no change only if something that stays still uses it; then name exactly what.",
        "Change the code only: never connect to, write to or change a database or any live system.",
    ]
    return "\n".join(lines).strip()


def send_task(kind: str, *, repo: str, node: Mapping[str, Any], text: str, note: str, by: str) -> str:
    """The task of a part sent for a fix or an analysis (a removal goes through its lead)."""
    head = f"{node.get('label')} in {repo}" + (f" ({node['path']})" if node.get("path") else "")
    asked = f"{by} sent it from a knowledge map" + (f": {note}" if note else ".")
    if kind == "fix":
        ask = [
            f"Fix what the evidence below shows about {head}, in the smallest change that solves it, with a test",
            "where the repository has tests, and open a pull request. If the evidence shows the part is dead or",
            "a leftover, remove it and everything that only it uses. If the right fix is not clear from the",
            "code, stop and say what you would need to know.",
        ]
    elif kind == "analysis":
        ask = [
            f"Investigate {head}, read-only, and report evidence-backed findings: what it really does today,",
            "whether the evidence below holds up in the code, what depends on it, and a recommendation (keep,",
            "fix or remove) with the exact change you would make as a diff, not applied.",
        ]
    else:
        raise ValueError(f"a part is not sent for {kind!r} this way")
    return "\n".join([*ask, "", asked, "", "What the map knows about it:", "", text]).strip()


def part_line(node: Mapping[str, Any], detail: Mapping[str, Any] | None, *, level: int = 0) -> str:
    """One part in a batch task: what it is, where, and why it was picked.

    ``level`` 0 says all of it, 1 the first reason and signal, 2 only its id, name and kind (the task then lists
    how parts are removed once, see :func:`batch_task`).
    """
    d = detail or {}
    head = f"- `{node.get('id')}` {node.get('label')} ({node.get('kind')})"
    if node.get("path") and level < 2:
        head += f", {node['path']}" + (f":{node['line']}" if node.get("line") else "")
    if level >= 2:
        return head
    bits: list[str] = []
    bloat = d.get("bloat") or node.get("bloat")
    if isinstance(bloat, Mapping) and bloat.get("score") is not None:
        reasons = [str(r.get("text")) for r in bloat.get("against") or [] if isinstance(r, Mapping)][: 3 - 2 * level]
        bits.append(f"bloat {bloat['score']}/100" + (f": {'; '.join(reasons)}" if reasons else ""))
    signals = [str(x) for x in node.get("signals") or []][: 3 - 2 * level]
    if signals:
        bits.append("signals: " + "; ".join(signals))
    narrative = d.get("narrative")
    if level == 0 and isinstance(narrative, Mapping) and narrative.get("does"):
        bits.append(f"does: {str(narrative['does'])[:240]}")
    if node.get("removal") and level == 0:
        bits.append(f"removed by: {node['removal']}")
    return head + (f". {' | '.join(bits)}" if bits else "")


class BatchTooLarge(ValueError):
    """Even one line per part does not fit one job's task: the batch must be sent in parts."""


def batch_task(
    kind: str,
    *,
    repo: str,
    parts: Sequence[tuple[Mapping[str, Any], Mapping[str, Any] | None]],
    map_title: str,
    note: str,
    by: str,
) -> str:
    """The task of one job for many parts of one repository (a batch send): one pull request, or one report."""
    n = len(parts)
    asked = f"{by} sent these {n} parts of the {map_title} map together" + (f": {note}" if note else ".")
    if kind == "remove":
        ask = [
            f"Remove these {n} parts from {repo}, in one pull request. A person marked each one as bloat: the marks",
            "are the decision, so do not re-assess whether to keep them. For each, delete it and everything only it",
            "uses, and drop every mention of it (package scripts, docs, READMEs, instruction files, config). A part",
            "that is not a file says how it is removed (removed by); where several are removed the same way (one",
            "migration for several tables), do them in one change. Run the repository's tests where it has them.",
            "Leave out any part that something staying still uses, and list each one left out with what uses it.",
            "Change the code only: never connect to, write to or change a database or any live system.",
        ]
    elif kind == "fix":
        ask = [
            f"Fix what the evidence below shows about these {n} parts of {repo}, in one pull request: the smallest",
            "change for each, with a test where the repository has tests. A part that turns out to be dead or a",
            "leftover is removed with what only it uses. List any part you could not fix and what you would need.",
            "Change the code only: never connect to, write to or change a database or any live system.",
        ]
    elif kind == "analysis":
        ask = [
            f"Investigate these {n} parts of {repo}, read-only, and report for each: what it really does today,",
            "whether the evidence below holds up in the code, what depends on it, and a recommendation (keep, fix",
            "or remove) with the change you would make as a diff, not applied. Group parts that share an answer.",
        ]
    else:
        raise ValueError(f"no batch task for {kind!r}")
    head = [*ask, "", asked, ""]
    for level in (0, 1, 2):
        lines = [part_line(node, detail, level=level) for node, detail in parts]
        notes = sorted({str(node["removal"]) for node, _ in parts if node.get("removal")}) if level else []
        how = ["How the parts that are not files are removed:", *[f"- {x}" for x in notes], ""] if notes else []
        text = "\n".join([*head, *how, f"The parts ({n}):", "", *lines]).strip()
        if len(text) <= MAX_BATCH_TASK:
            return text
    raise BatchTooLarge(f"{n} parts do not fit one job; send them in smaller batches")


def mark_counts(marks: Iterable[Mapping[str, Any]], me: str | None) -> dict[str, dict[str, Any]]:
    """Per part: how many marked it bloat, keep or not sure, and ``me``'s own verdict."""
    out: dict[str, dict[str, Any]] = {}
    for m in marks:
        c = out.setdefault(str(m["node_id"]), {"bloat": 0, "keep": 0, "unsure": 0, "mine": None})
        if m["verdict"] in VERDICTS:
            c[m["verdict"]] += 1
        if me is not None and str(m["marked_by"]).lower() == me.lower():
            c["mine"] = m["verdict"]
    return out

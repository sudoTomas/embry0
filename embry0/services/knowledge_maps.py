"""Knowledge maps (spec 2026-10-27): a scanner's map of a codebase or an organization, marked and sent by people.

A scanner outside embry0 reads a codebase (or every system of an
organization) and uploads what it found as one ``embry0.map/1`` document:
the parts (modules, files, tables, services, ...) laid out on a plane, the
links between them, and a detail record per part (what it does and why it
exists, its bloat score and the reasons, how much it is used, its history).
embry0 keeps the last upload per map and shows it on the Maps page.

People mark parts: bloat, keep or not sure. A bloat mark on a part that
lives in a repository becomes a ``weed`` lead, offered to the repository's
owners like every lead; sending the part accepts it, and the agent removes
the part in a pull request without arguing for keeping it, because the mark
is the decision. A part can also be sent for a fix or a read-only analysis,
which start a job directly. This module is the pure part: reading an
upload, building the lead and the tasks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from embry0.services.leads import Lead

FORMAT = "embry0.map/1"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
#: Upper bounds on one upload: past these it is refused, not cut.
MAX_NODES = 20_000
MAX_EDGES = 100_000
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
    return value if isinstance(value, str) and _REPO.match(value) else None


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
    if not SLUG.match(slug):
        raise MapError("a map's slug is lowercase letters, digits and hyphens, at most 64")
    if not isinstance(doc, Mapping):
        raise MapError("the upload is not a JSON object")
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
    return map_repo if map_repo and node.get("path") else None


def source_key(slug: str, node_id: str) -> str:
    return f"{SOURCE_PREFIX}{slug}:{node_id}"


def part_url(base: str, slug: str, node_id: str) -> str:
    from urllib.parse import quote

    return f"{base.rstrip('/')}/maps/{quote(slug)}?part={quote(node_id, safe='')}"


def part_text(node: Mapping[str, Any], detail: Mapping[str, Any] | None, map_title: str) -> str:
    """Everything the map knows about a part, in plain words: what an owner reads and what an agent is given."""
    d = detail or {}
    lines = [f"Part: {node.get('label')} ({node.get('kind')}) on the {map_title} map, id `{node.get('id')}`."]
    if node.get("path"):
        lines.append(f"File: {node['path']}" + (f":{node['line']}" if node.get("line") else ""))
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
    map_title: str,
    map_repo: str | None,
    node: Mapping[str, Any],
    detail: Mapping[str, Any] | None,
    marks: Sequence[Mark],
    url: str | None,
) -> Lead | None:
    """The weed lead a part marked bloat becomes; None when the part lives in no repository."""
    repo = part_repo(map_repo, node)
    if repo is None:
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

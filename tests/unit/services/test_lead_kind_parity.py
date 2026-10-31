"""Lead kinds (#166): every place outside the catalogue that spells a kind out agrees with it.

The catalogue (``embry0/services/lead_kinds.py``) is the one list. A few places
must still spell kinds out on their own, because they cannot import it: the
art manifest (``tests/unit/docs/test_world_art_manifest.py``), the MCP tools'
descriptions (``embry0.mcp_server`` is a thin client that imports nothing of
embry0), and the stored kinds' CHECK constraints
(``tests/unit/storage/test_migration_123.py``). The rest read it. Each test
here fails, naming the kind, when a kind is added in one place and not the
others.

Imports are inside the tests, so this file runs against a tree without the
catalogue's newer parts and each test fails there on its own assertion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "frontend" / "src"


def _proposal_kinds() -> tuple[str, ...]:
    from embry0.services.lead_kinds import ids

    return ids(proposal=True)


@pytest.mark.parametrize("kind", _proposal_kinds())
def test_a_job_brief_carries_no_proposal_id_of_any_kind(kind: str) -> None:
    """A job brief takes ids out rather than refusing them; the pattern once listed eight kinds by hand
    and kept a crack's or a weed's id."""
    from embry0.services.job_briefs import parse_job_brief

    answer = {
        "title": "Find why the worker crash-looped",
        "what": f"The worker failed to boot, as {kind}:00000000000000a1 says.",
        "why": "A boot crash takes every page down.",
        "done": "The boot error is logged. You get a pull request.",
        "size": "medium",
    }
    brief = parse_job_brief(json.dumps(answer))
    assert f"{kind}:" not in brief.what, f"a job brief kept the id of a {kind} proposal: {brief.what!r}"


def _named_in_descriptions() -> dict[str, list[str]]:
    """The kinds each MCP proposal tool's description names, as it quotes them: ``"case" (...)`` in the list's,
    ``"case:..."`` in the list's and the read's, and every quoted word of the start's."""
    from embry0.mcp_server import server

    listing = server.proposal_list.__doc__ or ""
    reading = server.proposal_get.__doc__ or ""
    starting = server.proposal_accept.__doc__ or ""
    return {
        "proposal_list": re.findall(r'"([a-z][\w-]*)" \(', listing),
        "proposal_get": re.findall(r'"([a-z][\w-]*):\.\.\."', listing + reading),
        "proposal_accept": re.findall(r'"([a-z][\w-]*)"', starting),
    }


def test_the_mcp_tools_name_every_kind_of_proposal_and_no_other() -> None:
    """An assistant routes on these descriptions: listing, reading and starting a proposal must each know every
    kind, or it never asks about the missing one, and name no kind the catalogue does not have, or it waits for
    one that never comes. The MCP server imports nothing of embry0, so its words are held here."""
    from embry0.mcp_server import server

    kinds = set(_proposal_kinds())
    named = _named_in_descriptions()
    differ = {tool: sorted(set(found) ^ kinds) for tool, found in named.items() if set(found) != kinds}
    assert differ == {}, f"an MCP tool's description and the catalogue's kinds of proposal differ: {differ}"
    listing = server.proposal_list.__doc__ or ""
    assert "ask_severities" in listing, "held is no longer only a ticket below the readiness floor"
    assert "lead_kinds" in listing and hasattr(server, "lead_kinds"), (
        "the description points at a tool an MCP client can call to learn every kind"
    )


def test_every_finder_is_a_lead_kind_of_the_catalogue() -> None:
    from embry0.services.lead_kinds import ids
    from embry0.settings.sections.leads import FINDERS

    unknown = [finder for finder in FINDERS if finder not in ids(lead=True)]
    assert unknown == [], (
        f"the leads section names finders whose kind the catalogue does not store as a lead: {unknown}"
    )


def test_every_leader_is_an_agent_every_deployment_has() -> None:
    """A quest of each kind is led by a character, which is an agent; the catalogue imports nothing of
    embry0, so the Slopmaster's name is written there and held to ``slop.SLOPMASTER`` here."""
    from embry0.services.lead_kinds import KINDS, UNKNOWN_KIND
    from embry0.services.slop import SLOPMASTER
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    strangers = {k.id: k.leader for k in (*KINDS.values(), UNKNOWN_KIND) if k.leader not in BUILTIN_SEED}
    assert strangers == {}, f"a kind is led by an agent no deployment has: {strangers}"
    assert (KINDS["weed"].leader, KINDS["slop"].leader) == (SLOPMASTER, SLOPMASTER), (
        "the Slopmaster pulls weeds and slop"
    )


def _frontend_sources() -> list[Path]:
    return [
        path
        for path in sorted(FRONTEND.rglob("*.ts*"))
        if "__tests__" not in path.parts and not path.name.endswith((".test.ts", ".test.tsx"))
    ]


#: A kind's name that is also another feature's word, so the scan leaves it out: why.
SHARED_WORDS = {"Slop": "the Slop Dungeon's and the Slop-O-Meter's word as much as the slop kind's plural"}
#: The same words in the dashboard that are not a kind's name, with why.
NOT_A_KIND = {
    (
        "frontend/src/components/world/QuestScroll.tsx",
        "Slop, not a bug",
    ): "a job's work nature, the slopometer's verdict",
    (
        "frontend/src/components/environments/RepoPreferencesSection.tsx",
        "open issues",
    ): "prose saying what reaches a repository's owners, not a kind's name on a badge or a filter",
}
#: A name followed by a colon, quoted or bare: an object key, a ``case`` label, a type's member.
_KEY = re.compile(r"""(?:(['"])([a-z][\w-]*)\1|\b([a-z][\w-]*))\s*:(?!:)""")


def code_of(text: str) -> str:
    """``text`` without its ``//`` and ``/* */`` comments, strings and JSX text kept: what the dashboard shows."""
    out: list[str] = []
    i, quote = 0, ""
    while i < len(text):
        char = text[i]
        if quote:
            out.append(char)
            if char == "\\":
                out.append(text[i + 1 : i + 2])
                i += 2
                continue
            if char == quote:
                quote = ""
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            if char in "'\"`":
                quote = char
            out.append(char)
        i += 1
    return "".join(out)


def block_keys(code: str) -> list[set[str]]:
    """Each brace block's own keys, nested blocks apart: an object's keys whatever their values (a nested object
    included), and a ``switch`` body's ``case`` labels whatever their bodies."""
    stack: list[set[str]] = [set()]
    blocks: list[set[str]] = []
    i, quote = 0, ""
    while i < len(code):
        char = code[i]
        if quote:
            i += 2 if char == "\\" else 1
            if char == quote:
                quote = ""
            continue
        found = _KEY.match(code, i)
        if found and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] in "_$.")):
            stack[-1].add(found.group(2) or found.group(3))
            i = found.end()
            continue
        if char in "'\"`":
            quote = char
        elif char == "{":
            stack.append(set())
        elif char == "}" and len(stack) > 1:
            blocks.append(stack.pop())
        i += 1
    return blocks + stack


def _names() -> list[str]:
    """Each kind's names as the dashboard could spell them: label and plural as written, and lowercase where that
    reads as the kind and nothing else (a name of two words or more, or a one-word plural such as "cracks")."""
    from embry0.services.lead_kinds import KINDS

    names = {n for k in KINDS.values() for n in (k.label, k.plural)} - set(SHARED_WORDS)
    for kind in KINDS.values():
        names.update(n.lower() for n in (kind.label, kind.plural) if " " in n)
        if " " not in kind.plural and kind.plural.lower() != kind.label.lower():
            names.add(kind.plural.lower())
    return sorted(names - {w.lower() for w in SHARED_WORDS}, key=lambda n: (-len(n), n))


def copies_in(code: str) -> list[str]:
    """What in ``code`` repeats the catalogue: each kind's name as a word (:func:`_names`, in any quotes or in JSX
    text), and each block (an object, a nested one's values included, or a ``switch``) or list that names three
    kinds or more by id."""
    from embry0.services.lead_kinds import KINDS

    found = [name for name in _names() if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", code)]
    for keys in block_keys(code):
        named = keys & set(KINDS)
        if len(named) >= 3:
            found.append("a table keyed by " + ", ".join(sorted(named)))
    for body in re.findall(r"\[([^\[\]]*)\]", code):
        listed = set(re.findall(r"['\"`]([a-z][\w-]*)['\"`]", body)) & set(KINDS)
        if len(listed) >= 3:
            found.append("a list of " + ", ".join(sorted(listed)))
    return found


def test_the_dashboard_keeps_no_copy_of_the_catalogue() -> None:
    """The dashboard names a kind only from GET /lead-kinds: a label or a table of kinds written into its own
    source is a copy that drifts (it once lacked crack and weed, so their badges showed raw ids)."""
    copies: dict[str, list[str]] = {}
    for path in _frontend_sources():
        where = str(path.relative_to(REPO))
        found = [c for c in copies_in(code_of(path.read_text(encoding="utf-8"))) if (where, c) not in NOT_A_KIND]
        if found:
            copies[where] = found
    assert copies == {}, f"the dashboard spells kinds on its own; read them from GET /lead-kinds: {copies}"
    stale = [entry for entry in NOT_A_KIND if entry[1] not in (REPO / entry[0]).read_text(encoding="utf-8")]
    assert stale == [], f"drop these from NOT_A_KIND, the file no longer says them: {stale}"


@pytest.mark.parametrize(
    ("planted", "caught"),
    [
        ('export const KIND_WORDS = { crack: "Crack", ci: "Failing checks" };', ["Failing checks", "Crack"]),
        ("export const KIND_WORDS = { crack: 'Cracks', ci: 'Failing checks' };", ["Failing checks", "Cracks"]),
        ("<p>Not asked in chat, Weeds and Cracks included:</p>", ["Cracks", "Weeds"]),
        (
            "export const KIND_ORDER = { case: 0, issue: 1, finding: 2, ticket: 3 };",
            ["a table keyed by case, finding, issue, ticket"],
        ),
        ('const ORDER = ["crack", "weed", "ci"];', ["a list of ci, crack, weed"]),
        ("// A crack the scout found: Cracks are mended.\nconst x = 1;", []),
        (
            'const ART = {\n  crack: { glyph: "pot", order: 1 },\n  weed: { glyph: "sprout", order: 2 },\n'
            '  ci: { glyph: "golem", order: 3 },\n};',
            ["a table keyed by ci, crack, weed"],
        ),
        (
            'switch (kind) {\n  case "crack": {\n    return 1;\n  }\n  case "weed": {\n    return 2;\n  }\n'
            '  case "ci": {\n    return 3;\n  }\n  default: {\n    return 0;\n  }\n}',
            ["a table keyed by ci, crack, weed"],
        ),
        ("<p>weeds and failing checks included</p>", ["failing checks", "weeds"]),
        ('const tagline = "Finds every crack, pulls one weed";', []),
        ("const label = cond ? left : right;", []),
    ],
    ids=[
        "double-quoted",
        "single-quoted",
        "jsx-text",
        "kind-table",
        "kind-list",
        "comment",
        "nested-object-table",
        "switch-cases",
        "lowercase",
        "lowercase-singular-prose",
        "ternary",
    ],
)
def test_the_check_for_a_copy_in_the_dashboard_can_fail(planted: str, caught: list[str]) -> None:
    """Each way the dashboard once copied, or could copy, the catalogue is caught; a comment is not a copy."""
    assert copies_in(code_of(planted)) == caught


@pytest.mark.parametrize(
    ("tool", "old", "new"),
    [
        ("proposal_get", '"weed:..."', '"weed:...", "omen:..."'),
        ("proposal_list", '"crack" (a crack', '"omen" (an omen), "crack" (a crack'),
        ("proposal_accept", '"crack"\n    or "weed"', '"crack", "omen"\n    or "weed"'),
    ],
)
def test_the_check_for_a_kind_only_the_mcp_descriptions_name_can_fail(
    monkeypatch: pytest.MonkeyPatch, tool: str, old: str, new: str
) -> None:
    """An extra kind is read from each description, the start's included (#300's review, plant q4)."""
    from embry0.mcp_server import server

    doc = getattr(server, tool).__doc__ or ""
    assert old in doc, f"{tool}'s description no longer says {old!r}"
    monkeypatch.setattr(getattr(server, tool), "__doc__", doc.replace(old, new))
    assert "omen" in _named_in_descriptions()[tool], f"a kind the catalogue does not have is read from {tool}"

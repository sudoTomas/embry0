"""Lead kinds (#166): one catalogue, and every place that says something per kind reads it.

Imports are inside the tests, so this file runs against a tree without the
catalogue and each test fails there on its own assertion or import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

T0 = datetime(2026, 10, 29, 12, tzinfo=UTC)


def _proposal(kind: str, **extra: Any) -> Any:
    from embry0.services.proposals import Proposal

    return Proposal(
        id=f"{kind}:0000000000000001",
        kind=kind,
        repo="acme/widgets",
        title="Something to do",
        summary="A sentence.",
        details="",
        action="Its own words for what a yes starts",
        created_at=T0,
        extra=extra,
    )


def test_a_brief_carrying_the_id_of_any_kind_of_proposal_is_refused() -> None:
    """A brief must never carry a proposal id, which says nothing to a person; the refusal once
    listed eight kinds by hand and let a crack's or a weed's id through."""
    from embry0.services.proposal_briefs import parse_brief
    from embry0.services.proposals import KINDS

    let_through = []
    for kind in KINDS:
        answer = (
            '{"title": "Fix it", "problem": "See '
            + kind
            + ':0000000000000001 for the rest.", "fix": "Fix it. You get a pull request.", "size": "small"}'
        )
        try:
            parse_brief(answer)
        except ValueError as exc:
            assert "proposal id" in str(exc), f"{kind}: refused for another reason: {exc}"
        else:
            let_through.append(kind)
    assert not let_through, f"a brief carrying the id of a {', '.join(let_through)} proposal was accepted"


def test_every_kind_is_named_in_full() -> None:
    from embry0.services.lead_kinds import KINDS, catalogue
    from embry0.services.leads import SEVERITIES

    assert list(KINDS) == [k["id"] for k in catalogue()], "GET /world serves the kinds in the catalogue's order"
    for kind in KINDS.values():
        for name in ("label", "plural", "about", "found_by", "starts", "verb", "creature", "leader"):
            assert getattr(kind, name).strip(), f"{kind.id} has no {name}"
        assert not kind.lead or kind.proposal, f"{kind.id} is stored as a lead, so it is offered as a proposal"
        assert kind.chat_severities is None or kind.chat_severities <= set(SEVERITIES), kind.id


@pytest.mark.parametrize(
    "kind", ["case", "issue", "finding", "ticket", "ci", "alert", "flaky", "review", "crack", "weed"]
)
def test_the_board_the_cards_and_the_briefs_read_a_kind_from_the_catalogue(kind: str) -> None:
    """One kind, one set of words: the quest board's verb, creature and leader, a decision card's
    label and what a yes starts, and a brief prompt's kind and yes all come from its entry."""
    from embry0.services.decisions import offered
    from embry0.services.lead_kinds import KINDS
    from embry0.services.proposal_briefs import user_prompt
    from embry0.services.world import lead_entry

    entry = KINDS[kind]
    board = lead_entry({"id": f"{kind}:1", "kind": kind})
    assert (board["verb"], board["creature"], board["leader"]) == (entry.verb, entry.creature, entry.leader)
    card = offered({"id": f"{kind}:1", "kind": kind, "repo": "acme/widgets", "title": "t"}, ask_min=60).to_dict()
    assert card["kind_label"] == entry.label and card["verdict"]["label"] == entry.label
    assert card["yes_starts"] == entry.starts
    prompt = user_prompt(_proposal(kind))
    assert f"Kind: {entry.about}\n" in prompt
    assert f"What a yes starts: {entry.starts} (Its own words for what a yes starts)" in prompt


def test_a_kind_the_catalogue_does_not_know_still_reads() -> None:
    from embry0.services.decisions import offered
    from embry0.services.proposal_briefs import user_prompt
    from embry0.services.world import lead_entry

    board = lead_entry({"id": "omen:1", "kind": "omen"})
    assert (board["verb"], board["creature"]) == ("Investigate", "mystery")
    omen = {"id": "omen:1", "kind": "omen", "repo": "acme/widgets", "title": "t", "action": "Look"}
    card = offered(omen, ask_min=60).to_dict()
    assert card["kind_label"] == "Omen" and card["yes_starts"] == "Look"
    assert "Kind: omen\n" in user_prompt(_proposal("omen"))

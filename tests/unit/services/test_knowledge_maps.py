"""Knowledge maps (spec 2026-10-27): reading an upload, the weed lead of a marked part, and the tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.services.knowledge_maps import (
    FORMAT,
    MapError,
    Mark,
    map_lead,
    mark_counts,
    parse_map,
    part_repo,
    part_text,
    part_url,
    removal_task,
    send_task,
    source_key,
)
from embry0.services.leads.tasks import lead_task

T0 = datetime(2026, 10, 27, 15, tzinfo=UTC)


def _doc(**over: Any) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "slug": "shop",
        "title": "Shop",
        "repo": "acme/shop",
        "commit": "abc",
        "generated_at": "2026-10-27T15:00:00Z",
        "kinds": [{"id": "script", "label": "Script"}],
        "areas": [{"id": "ops", "label": "Ops", "x": 1, "y": 2, "r": 3}],
        "nodes": [
            {
                "id": "script:probe.mjs",
                "kind": "script",
                "label": "probe.mjs",
                "path": "scripts/probe.mjs",
                "x": 1.26,
                "y": 2,
                "bloat": {"score": 70, "band": "bloat"},
                "url": "https://forge.example.com/acme/shop/blob/abc/scripts/probe.mjs",
            },
            {"id": "table:orders", "kind": "table", "label": "orders", "x": 0, "y": 0, "url": "javascript:alert(1)"},
            {"id": "sys:billing", "kind": "system", "label": "billing", "repo": "acme/billing"},
        ],
        "edges": [
            {"s": "script:probe.mjs", "t": "table:orders", "rel": "reads"},
            {"s": "script:probe.mjs", "t": "gone"},
        ],
        "details": {"script:probe.mjs": {"narrative": {"does": "Prints one row."}}, "elsewhere": {"x": 1}},
        **over,
    }


def test_an_upload_is_read_into_what_the_viewer_draws_and_a_record_per_part() -> None:
    parsed = parse_map(_doc(), "shop")
    assert (parsed.slug, parsed.title, parsed.repo, parsed.commit) == ("shop", "Shop", "acme/shop", "abc")
    nodes = {n["id"]: n for n in parsed.graph["nodes"]}
    assert nodes["script:probe.mjs"]["x"] == 1.3
    assert nodes["script:probe.mjs"]["bloat"] == {"score": 70, "band": "bloat"}
    assert nodes["table:orders"]["url"] is None, "only an https link is kept"
    assert parsed.graph["edges"] == [{"s": "script:probe.mjs", "t": "table:orders", "rel": "reads"}]
    assert set(parsed.details) == {"script:probe.mjs"}, "a record for a part the map does not have is dropped"


@pytest.mark.parametrize(
    ("doc", "slug", "message"),
    [
        (_doc(format="other/1"), "shop", "format must be"),
        (_doc(), "Shop!", "slug is lowercase"),
        (_doc(slug="else"), "shop", "not the one in the path"),
        (_doc(nodes=[{"id": "a"}, {"id": "a"}]), "shop", "there twice"),
        (_doc(nodes=[{"label": "no id"}]), "shop", "needs an id"),
        ([], "shop", "not a JSON object"),
    ],
)
def test_an_upload_that_is_not_a_map_says_why(doc: Any, slug: str, message: str) -> None:
    with pytest.raises(MapError, match=message):
        parse_map(doc, slug)


def test_a_part_lives_in_its_own_repository_else_the_maps_when_it_is_a_file() -> None:
    assert part_repo("acme/shop", {"path": "a.py"}) == "acme/shop"
    assert part_repo("acme/shop", {"path": None}) is None
    assert part_repo(None, {"repo": "acme/billing"}) == "acme/billing"
    assert part_repo(None, {"path": "a.py"}) is None


def test_a_part_marked_bloat_becomes_a_weed_lead_that_names_who_marked_it() -> None:
    node = parse_map(_doc(), "shop").graph["nodes"][0]
    marks = [Mark("ann@example.com", "bloat", "dead probe", T0), Mark("bo@example.com", "keep", "", T0)]
    url = part_url("https://embry0.example.com/", "shop", node["id"])
    lead = map_lead(
        slug="shop",
        map_title="Shop",
        map_repo="acme/shop",
        node=node,
        detail={"narrative": {"does": "Prints one row."}},
        marks=marks,
        url=url,
    )
    assert lead is not None
    assert (lead.kind, lead.repo, lead.source_key) == ("weed", "acme/shop", source_key("shop", "script:probe.mjs"))
    assert lead.title == "Remove probe.mjs"
    assert lead.summary == "ann@example.com marked probe.mjs bloat on the Shop map: dead probe"
    assert lead.paths == ("scripts/probe.mjs",)
    assert lead.data == {"source": "map", "map": "shop", "node": "script:probe.mjs", "marked_by": ["ann@example.com"]}
    assert url == "https://embry0.example.com/maps/shop?part=script%3Aprobe.mjs"
    assert "What it does: Prints one row." in lead.details
    assert (
        map_lead(
            slug="shop",
            map_title="Shop",
            map_repo=None,
            node={"id": "x", "label": "x"},
            detail=None,
            marks=marks,
            url=None,
        )
        is None
    )


def test_a_maps_weed_is_removed_without_re_assessing_and_the_scouts_weed_keeps_its_own_task() -> None:
    row = {
        "kind": "weed",
        "repo": "acme/shop",
        "title": "Remove probe.mjs",
        "summary": "ann marked probe.mjs bloat on the Shop map",
        "details": "Part: probe.mjs",
        "area": None,
        "data": {"source": "map", "paths": ["scripts/probe.mjs"], "labels": []},
    }
    task = lead_task(row)
    assert task == removal_task(row)
    assert task.startswith("Remove probe.mjs from acme/shop.")
    assert "do not re-assess whether to keep it" in task
    assert "Where: scripts/probe.mjs" in task
    assert "never connect to, write to or change a database or any live system" in task
    scout = lead_task({**row, "title": "Weed: unused helper", "data": {"pull": "- x", "paths": [], "labels": []}})
    assert "Pull a weed the scout found" in scout and "re-assess" not in scout


def test_a_fix_or_an_analysis_carries_what_the_map_knows_and_who_sent_it() -> None:
    node = {"id": "script:probe.mjs", "label": "probe.mjs", "path": "scripts/probe.mjs", "kind": "script"}
    text = part_text(
        node, {"bloat": {"score": 70, "band": "bloat", "against": [{"text": "Nothing runs it.", "w": 70}]}}, "Shop"
    )
    assert "Bloat score: 70/100 (bloat)." in text and "- +70 Nothing runs it." in text
    fix = send_task("fix", repo="acme/shop", node=node, text=text, note="please", by="ann")
    assert fix.startswith("Fix what the evidence below shows about probe.mjs in acme/shop (scripts/probe.mjs)")
    assert "ann sent it from a knowledge map: please" in fix
    analysis = send_task("analysis", repo="acme/shop", node=node, text=text, note="", by="ann")
    assert "read-only" in analysis and "not applied" in analysis
    with pytest.raises(ValueError):
        send_task("remove", repo="acme/shop", node=node, text=text, note="", by="ann")


def test_marks_are_counted_per_part_with_the_viewers_own() -> None:
    rows = [
        {"node_id": "a", "marked_by": "Ann@example.com", "verdict": "bloat"},
        {"node_id": "a", "marked_by": "bo", "verdict": "keep"},
        {"node_id": "b", "marked_by": "bo", "verdict": "unsure"},
    ]
    assert mark_counts(rows, "ann@example.com") == {
        "a": {"bloat": 1, "keep": 1, "unsure": 0, "mine": "bloat"},
        "b": {"bloat": 0, "keep": 0, "unsure": 1, "mine": None},
    }


def test_only_a_file_in_a_repository_can_be_removed_or_become_a_lead() -> None:
    from embry0.services.knowledge_maps import removable

    system = {"id": "sys:billing", "label": "billing", "repo": "acme/billing", "path": None}
    marks = [Mark("ann", "bloat", "", T0)]
    assert removable(None, system) is False, "a whole system is not removed by a code change"
    assert removable("acme/shop", {"id": "f", "path": "scripts/a.py"}) is True
    assert removable(None, {"id": "f", "path": "scripts/a.py"}) is False, "a path with no repository"
    lead = map_lead(slug="org", map_title="Org", map_repo=None, node=system, detail=None, marks=marks, url=None)
    assert lead is None


def test_a_part_the_scanner_says_how_to_remove_is_removable_without_a_file() -> None:
    from embry0.services.knowledge_maps import removable

    table = {"id": "table:old_backup", "label": "old_backup", "kind": "table", "removal": "Drop it in a new migration."}
    assert part_repo("acme/shop", table) == "acme/shop"
    assert removable("acme/shop", table) is True
    assert removable("acme/shop", {**table, "removal": None}) is False
    assert "How it is removed: Drop it in a new migration." in part_text(table, None, "Shop")
    parsed = parse_map(_doc(nodes=[{"id": "t", "removal": "x" * 900}]), "shop")
    assert len(parsed.graph["nodes"][0]["removal"]) == 600


def test_a_batch_is_one_task_for_one_pull_request_that_lists_every_part() -> None:
    from embry0.services.knowledge_maps import MAX_BATCH_TASK, batch_task

    parts = [
        (
            {
                "id": f"table:t{i}",
                "label": f"t{i}",
                "kind": "table",
                "removal": "Drop it in a migration.",
                "signals": ["empty"],
            },
            {"bloat": {"score": 70, "band": "bloat", "against": [{"text": "a backup table", "w": 70}]}},
        )
        for i in range(3)
    ]
    task = batch_task("remove", repo="acme/shop", parts=parts, map_title="Shop", note="old backups", by="ann")
    assert task.startswith("Remove these 3 parts from acme/shop, in one pull request.")
    flat = " ".join(task.split())
    assert "do not re-assess" in flat and "one migration for several tables" in flat
    assert "ann sent these 3 parts of the Shop map together: old backups" in task
    assert task.count("- `table:t") == 3 and "removed by: Drop it in a migration." in task
    assert "never connect to, write to or change a database" in task
    big = [
        (
            {"id": f"f{i}", "label": "x" * 150, "kind": "script", "path": "a/" + "b" * 150, "signals": ["s" * 290] * 3},
            None,
        )
        for i in range(200)
    ]
    long = batch_task("fix", repo="acme/shop", parts=big, map_title="Shop", note="", by="ann")
    assert len(long) <= MAX_BATCH_TASK and long.count("- `f") == 200, "a long batch drops detail, never parts"
    from embry0.services.knowledge_maps import BatchTooLarge

    huge = [({"id": "x" * 300 + str(i), "label": "y" * 200, "kind": "k"}, None) for i in range(200)]
    with pytest.raises(BatchTooLarge):
        batch_task("fix", repo="acme/shop", parts=huge, map_title="Shop", note="", by="ann")
    tables = [
        ({"id": f"table:t{i}", "label": f"t{i}", "kind": "table", "removal": "Drop it."}, None) for i in range(150)
    ]
    short = batch_task("remove", repo="acme/shop", parts=tables * 1, map_title="Shop", note="", by="ann")
    assert short.count("- `table:t") == 150
    assert "read-only" in batch_task("analysis", repo="acme/shop", parts=parts, map_title="Shop", note="", by="ann")
    with pytest.raises(ValueError):
        batch_task("delete", repo="acme/shop", parts=parts, map_title="Shop", note="", by="ann")

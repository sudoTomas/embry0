"""The progression vocabulary is pinned to the code that implements it (review of #207, finding 2).

Each name a pack may use must have exactly the code behind it: a metric its
SQL fragment (binding exactly its parameters) or its reading from the
level-ups, a quest kind its evaluator, a diary task kind its own. Without
these pins a new or renamed entry would validate and then count nothing.
"""

from __future__ import annotations

import re

from embry0.services import progression_vocabulary as vocab
from embry0.services import quests
from embry0.services.achievements import DERIVED_METRICS
from embry0.storage.repositories.progression import FRAGMENTS


def test_every_fragment_binds_exactly_its_metrics_parameters() -> None:
    for metric_id, metric in vocab.METRICS.items():
        if metric.fragment is None:
            continue
        names = set(re.findall(r"\{(\w+)\}", FRAGMENTS[metric.fragment])) - {"tz"}
        assert names == set(metric.params.model_fields), f"{metric_id}: the fragment binds {sorted(names)}"


def test_every_sql_metric_has_its_fragment_and_every_fragment_a_metric() -> None:
    used = {m.fragment for m in vocab.METRICS.values() if m.fragment is not None}
    assert used == set(FRAGMENTS), "the vocabulary and the repository name the same fragments"


def test_every_metric_without_a_fragment_is_read_from_the_level_ups() -> None:
    assert {m for m, metric in vocab.METRICS.items() if metric.fragment is None} == DERIVED_METRICS, (
        "a metric with no fragment that metric_steps does not read would count nothing"
    )


def test_every_quest_kind_has_its_evaluator_and_the_capstone_its_own() -> None:
    capstones = {k for k, kind in vocab.QUEST_KINDS.items() if kind.capstone}
    assert capstones == {"capstone"}, "the capstone is the one quest kind that reads the levels and the diaries"
    assert set(quests._KINDS) == set(vocab.QUEST_KINDS) - capstones, (
        "every kind but the capstone is evaluated from the journal, and none is taken for a capstone by default"
    )


def test_every_diary_task_kind_has_its_evaluator() -> None:
    assert set(quests._TASKS) == set(vocab.TASK_KINDS), "a task kind without its evaluator would fail on every read"

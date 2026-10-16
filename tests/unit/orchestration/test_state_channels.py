"""#304 — close the INVALID_CONCURRENT_GRAPH_UPDATE class, not the instance.

A plain TypedDict key becomes a LangGraph ``LastValue`` channel, which raises
(killing the job) when it receives two writes in one superstep. Which keys
can collide is not statically knowable: node updates are built with spreads
(``{**result, **cap_updates}``) and helper returns, and on the double-resume
path the SAME node runs twice, so even a single-writer key collides. The
enforceable rule is therefore: NO JobState key may be a plain LastValue.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest
from langgraph.channels.last_value import LastValue
from structlog.testing import capture_logs

from embry0.orchestration.channels import FoldingLastValue, PreferNonEmpty, UnionOfLists
from embry0.orchestration.state import JobState


def _workflows() -> dict[str, type]:
    """Every compiled graph whose state schema is JobState."""
    from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
    from embry0.workflows.onboard.graph import OnboardWorkflow
    from embry0.workflows.pr_review.graph import PrReviewWorkflow
    from embry0.workflows.qa.graph import QAWorkflow

    return {
        "issue-to-pr": IssueToprWorkflow,
        "onboard": OnboardWorkflow,
        "pr-review": PrReviewWorkflow,
        "qa": QAWorkflow,
    }


_GRAPHS = sorted(_workflows())


def _channels(graph: str = "issue-to-pr"):
    return _workflows()[graph]().compile().channels


def test_every_job_state_graph_is_covered() -> None:
    """A new StateGraph(JobState) must be added to _workflows() above."""
    import pathlib
    import re

    import embry0

    root = pathlib.Path(embry0.__file__).parent
    users = {p.parent.name for p in root.rglob("*.py") if re.search(r"StateGraph\(JobState\)", p.read_text())}
    assert users == {"issue_to_pr", "onboard", "pr_review", "qa"}


@pytest.mark.parametrize("graph", _GRAPHS)
def test_no_job_state_key_is_a_plain_last_value_channel(graph: str) -> None:
    channels = _channels(graph)
    plain = sorted(key for key in get_type_hints(JobState) if type(channels[key]) is LastValue)

    assert plain == [], (
        f"JobState keys {plain} would crash the job on a second write in one superstep — "
        "annotate them with a reducer or embry0.orchestration.channels.FoldingLastValue"
    )


@pytest.mark.parametrize("graph", _GRAPHS)
@pytest.mark.parametrize("key", sorted(get_type_hints(JobState)))
def test_every_job_state_channel_survives_two_writes_in_one_superstep(graph: str, key: str) -> None:
    channel = _channels(graph)[key].copy()
    sample = [] if key in ("agent_outputs", "errors") else None  # operator.add keys need addable values

    channel.update([sample, sample])  # InvalidUpdateError before #304 for every plain key


def test_folding_last_value_is_last_value_for_a_single_write() -> None:
    channel = FoldingLastValue(str, "k")
    assert not channel.is_available()  # no injected default: an unwritten key stays absent
    assert channel.update([]) is False
    assert channel.update(["a"]) is True and channel.get() == "a"
    assert channel.update([None]) is True and channel.get() is None  # a sequential clear still clears


def test_folding_last_value_takes_the_last_of_concurrent_writes() -> None:
    channel = FoldingLastValue(str, "k")
    channel.update(["a", "b"])
    assert channel.get() == "b"


def test_prefer_non_empty_keeps_a_value_over_a_concurrent_clear() -> None:
    """pr_url / sandbox_container_id / pending_agent_questions: losing the
    value to a concurrent None/[] write would strand a PR or drop questions."""
    channel = PreferNonEmpty(str, "pr_url")
    channel.update(["https://github.com/o/r/pull/1", None])
    assert channel.get() == "https://github.com/o/r/pull/1"
    channel.update(["", "container-1", None])
    assert channel.get() == "container-1"


def test_prefer_non_empty_still_lets_a_sequential_clear_through() -> None:
    channel = PreferNonEmpty(str, "pr_url")
    channel.update(["u"])
    channel.update([None])
    assert channel.get() is None


def test_folding_channels_round_trip_through_a_checkpoint() -> None:
    """Same checkpoint shape as LastValue, so paused jobs resume across the deploy."""
    channel = PreferNonEmpty(str, "pr_url")
    channel.update(["u"])
    restored = channel.from_checkpoint(channel.checkpoint())
    assert type(restored) is PreferNonEmpty and restored.get() == "u"
    assert LastValue(str, "pr_url").from_checkpoint(channel.checkpoint()).get() == "u"


@pytest.mark.parametrize("key", ["pr_url", "sandbox_container_id"])
def test_keys_that_must_not_lose_a_value_use_prefer_non_empty(key: str) -> None:
    assert type(_channels()[key]) is PreferNonEmpty


# ---- pending_agent_questions: a question must never vanish -------------------

_Q1 = {"question": "Which env?", "asking_node": "developer", "importance": "blocking"}
_Q2 = {"question": "Which env?", "asking_node": "review", "importance": "blocking"}  # same text, other asker


def test_pending_agent_questions_uses_the_union_channel() -> None:
    assert type(_channels()["pending_agent_questions"]) is UnionOfLists


def test_union_keeps_every_concurrent_question_once_in_task_order() -> None:
    channel = UnionOfLists(list, "pending_agent_questions")
    channel.update([[_Q1], [], [_Q2, _Q1], None])
    assert channel.get() == [_Q1, _Q2]


def test_union_is_plain_replacement_for_a_single_write() -> None:
    channel = UnionOfLists(list, "pending_agent_questions")
    assert not channel.is_available()
    channel.update([[_Q1]])
    channel.update([[_Q2]])  # a later node's questions REPLACE, never accumulate
    assert channel.get() == [_Q2]
    channel.update([[]])  # ask_user_interrupt clearing answered questions
    assert channel.get() == []


def test_union_of_only_empty_concurrent_writes_is_the_last_one() -> None:
    channel = UnionOfLists(list, "pending_agent_questions")
    channel.update([None, []])
    assert channel.get() == []


# ---- M1: folding is never silent ---------------------------------------------


@pytest.mark.parametrize(
    ("channel_cls", "values", "kept_type"),
    [
        (FoldingLastValue, ["SECRET-a", "SECRET-b"], "str"),
        (PreferNonEmpty, ["SECRET-url", None], "str"),
        (UnionOfLists, [[_Q1], [_Q2]], "list"),
    ],
)
def test_concurrent_writes_are_logged_without_the_values(channel_cls: type, values: list, kept_type: str) -> None:
    channel = channel_cls(str, "result_summary")

    with capture_logs() as logs:
        channel.update(values)

    assert logs == [
        {
            "event": "state_channel_folded_concurrent_writes",
            "log_level": "warning",
            "key": "result_summary",
            "count": 2,
            "kept_type": kept_type,
            "channel": channel_cls.__name__,
        }
    ]
    assert "SECRET" not in repr(logs) and "Which env" not in repr(logs)


@pytest.mark.parametrize("channel_cls", [FoldingLastValue, PreferNonEmpty, UnionOfLists])
def test_a_single_write_logs_nothing(channel_cls: type) -> None:
    channel = channel_cls(str, "result_summary")

    with capture_logs() as logs:
        channel.update([])
        channel.update([["x"]])

    assert logs == []


async def test_a_real_two_writer_superstep_logs_the_fold_with_the_state_key() -> None:
    """The reviewer's repro: a real fan-out over JobState used to fold with
    zero log lines where main raises loudly."""
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(JobState)
    graph.add_node("a", lambda _s: {"result_summary": "from a", "pr_url": "https://x/pull/1"})
    graph.add_node("b", lambda _s: {"result_summary": "from b", "pr_url": None})
    for node in ("a", "b"):
        graph.add_edge(START, node)
        graph.add_edge(node, END)

    with capture_logs() as logs:
        result = await graph.compile().ainvoke({"job_id": "j"})

    folded = {entry["key"]: entry for entry in logs if entry["event"] == "state_channel_folded_concurrent_writes"}
    assert set(folded) == {"result_summary", "pr_url"}
    assert folded["pr_url"]["count"] == 2 and folded["pr_url"]["kept_type"] == "str"
    assert result["pr_url"] == "https://x/pull/1"

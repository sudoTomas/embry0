"""Issue-to-PR workflow nodes — real implementations with LangGraph native patterns.

Each node that runs an agent:
1. Receives infrastructure via config["configurable"] (RunnableConfig injection)
2. Emits live events via get_stream_writer()
3. Can pause for human input via interrupt()

RAV-1235: formerly one ~2.2k-line module; now a package with one module per
node (`_init`, `_triage`, `_developer`, `_review`, `_interrupts`) plus shared
helpers (`_common`). This ``__init__`` is the package's single public
namespace: every import site (graph.py, the QA workflow, tests) keeps using
``embry0.workflows.issue_to_pr.nodes``.

Seam ordering matters: the shared collaborators below (``run_agent_node``,
``get_stream_writer``, ``interrupt``) are bound BEFORE the node modules are
imported, and the node modules call them through this namespace at call time
— so a test patch on ``...nodes.run_agent_node`` reaches every node.
"""

from __future__ import annotations

# --- shared seams (bind first — see docstring) ---
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from embry0.orchestration.nodes.agent import (
    BRAINSTORMING_ASK_USER_CAP,
    DEFAULT_ASK_USER_CAP,
    USER_RETRY_ROUNDS_CAP,
    run_agent_node,
)

# --- node modules (import after the seams) ---
from embry0.workflows.issue_to_pr.nodes._common import (
    _extract_ask_user_events,
    _filter_user_env_for_sandbox,
    _format_question_text,
    _format_user_answers_block,
)
from embry0.workflows.issue_to_pr.nodes._developer import (
    _branch_on_origin,
    _build_developer_adopted_prompt,
    _build_developer_delta_prompt,
    _build_developer_full_prompt,
    _commit_unfinished_work,
    _count_unpushed_commits,
    _developer_branch_name,
    _developer_delta_sections,
    _has_uncommitted_work,
    _rescue_push_branch,
    _verify_branch_pushed,
    developer_node,
)
from embry0.workflows.issue_to_pr.nodes._init import init_node
from embry0.workflows.issue_to_pr.nodes._interrupts import (
    ask_user_interrupt,
    max_retries_node,
    retry_node,
)
from embry0.workflows.issue_to_pr.nodes._review import review_node
from embry0.workflows.issue_to_pr.nodes._triage import (
    _extract_qa_decision_from_events,
    _extract_qa_decision_from_triage_dict,
    _route_qa_failure_action,
    triage_node,
)
from embry0.workflows.issue_to_pr.nodes._verify import verify_node

__all__ = [
    "BRAINSTORMING_ASK_USER_CAP",
    "DEFAULT_ASK_USER_CAP",
    "USER_RETRY_ROUNDS_CAP",
    "_branch_on_origin",
    "_build_developer_adopted_prompt",
    "_build_developer_delta_prompt",
    "_build_developer_full_prompt",
    "_developer_branch_name",
    "_developer_delta_sections",
    "_extract_ask_user_events",
    "_extract_qa_decision_from_events",
    "_extract_qa_decision_from_triage_dict",
    "_filter_user_env_for_sandbox",
    "_format_question_text",
    "_format_user_answers_block",
    "_route_qa_failure_action",
    "_commit_unfinished_work",
    "_count_unpushed_commits",
    "_has_uncommitted_work",
    "_rescue_push_branch",
    "_verify_branch_pushed",
    "ask_user_interrupt",
    "developer_node",
    "get_stream_writer",
    "init_node",
    "interrupt",
    "max_retries_node",
    "retry_node",
    "review_node",
    "run_agent_node",
    "triage_node",
    "verify_node",
]

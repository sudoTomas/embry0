"""Base state schemas for LangGraph orchestration."""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field

from embry0.orchestration.channels import FoldingLastValue, PreferNonEmpty, UnionOfLists


class TriageAction(StrEnum):
    PROCEED = "proceed"
    NEEDS_INFO = "needs_info"
    SPLIT = "split"


class JobKind(StrEnum):
    """Fixed job-kind vocabulary emitted by triage (RAV-601).

    Deliberately a closed enum validated at the triage-parse boundary — a
    free-form kind could route to nonexistent pipeline nodes. Extend by
    adding a member AND a route mapping (route_plan.AGENT_TYPE_TO_NODE
    covers agent types; kinds resolve to templates via
    pipeline_templates.default_for_kind).
    """

    CODE = "code"  # changes to a repository
    RESEARCH = "research"  # information gathering / synthesis
    ANALYSIS = "analysis"  # examining provided material
    OPS = "ops"  # operational / environment tasks


class AskUserChannel(StrEnum):
    """Outbound channels for agent ask-user questions.

    Per-issue list stored in ``issues.notification_channels`` (jsonb). When an
    agent calls ``embry0.sandbox.ask_user``, the dispatcher fans out the
    question to every channel selected on the originating issue. Used as the
    typed validation set on the API request side; responses serialize as
    plain strings so dashboard clients don't have to import the enum.
    """

    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    GITHUB = "github"
    # #163: the issue's originating tracker as an explicitly selectable
    # channel. The tracker trail also posts unconditionally for the
    # originating tracker; selecting it here only matters for issues whose
    # notification_channels are curated (the executor dedupes).
    LINEAR = "linear"
    GITLAB = "gitlab"
    # Outbound Slack incoming webhook. No reply path.
    SLACK = "slack"


class PipelineConfig(TypedDict, total=False):
    sandbox_profile: str
    max_feedback_loops: int
    reviewer_enabled: bool
    validator_modes: list[str]
    agent_models: dict[str, str]
    agent_tools: dict[str, list[str]]
    # A bare list replaces the agent's skills; {"add": [...], "remove": [...] | "*"}
    # is a delta over them (embry0.agents.skills.effective_skills).
    agent_skills: dict[str, list[str] | dict[str, Any]]
    budget_usd: float
    # Phase 1 additions — per-agent-type overrides produced by triage.
    execution_modes: dict[str, str]
    auth_modes: dict[str, str]
    system_prompts: dict[str, str]
    mcp_servers: dict[str, dict[str, Any]]
    # EMB-35: per-agent turn budgets, e.g. {"developer": 60}. Missing agents
    # fall back to DEFAULT_AGENT_MAX_TURNS in orchestration/nodes/agent.py.
    agent_max_turns: dict[str, int]


_PIPELINE_DEFAULTS: dict[str, Any] = {
    "max_feedback_loops": 5,
    "reviewer_enabled": True,
    "validator_modes": ["test", "lint", "typecheck"],
}


def make_pipeline_config(**kwargs: Any) -> PipelineConfig:
    merged: dict[str, Any] = {**_PIPELINE_DEFAULTS, **kwargs}
    # cast() avoids the TypedDict(**dict[str, Any]) unsafe-expansion error;
    # the caller controls kwargs so shape is known to be correct.
    return cast(PipelineConfig, merged)


class TriageDecision(TypedDict, total=False):
    action: str
    confidence: float
    job_kind: str  # JobKind value; defaults to "code"
    pipeline_template: str
    pipeline_config: PipelineConfig
    questions: list[Any]  # list of strings or dicts with question/importance/suggested_answer
    sub_tasks: list[dict[str, Any]]
    reasoning: str
    duplicate_of_pr: dict[str, Any] | None  # RAV-1079: {pr_number, reason} when the task duplicates an open PR
    work_nature: dict[str, Any] | None  # slop dungeon: the slopometer's verdict (embry0.services.slop)


class AgentOutputEntry(TypedDict, total=False):
    agent_type: str
    is_error: bool
    error_message: str
    output: str
    cost_usd: float
    duration_ms: int
    tools_called: dict[str, int]
    # Safety-policy denials, keyed by reason (see AgentOutput.policy_denials).
    policy_denials: dict[str, int]
    # EMB-35: per-run token usage from the executor's ResultMessage.usage.
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    # #304: the runner's own diagnosis of an errored run (an ErrorCode value,
    # e.g. ERR_SANDBOX_IMAGE_STALE). Read at job end off the TERMINAL output
    # only — never copied to state["error_code"], which is job-terminal.
    error_code: str | None
    # #466: ISO-8601 (UTC) reset of a spent usage window, on an
    # ERR_PROVIDER_QUOTA_EXHAUSTED output only. A string, not a datetime, so
    # the checkpoint holds plain JSON. The outcome hook quarantines the
    # account until then.
    provider_resets_at: str | None
    # Ledger provider of the account that served this run ("anthropic" or
    # "xai"). The outcome hook quarantines only this provider's llm use.
    provider: str
    # Spent account waits: the wait (AccountWait.record()) a run in a pipeline
    # that parks at a point of its own leaves for that point to raise.
    account_wait: dict[str, Any]
    # Plan C: post-run conversation state forwarded by run_agent_node so
    # workflow nodes (developer/triage/review) can persist via
    # AgentSessionsRepository. ``messages`` for api_key mode;
    # ``session_id`` + ``session_blob`` (bytes pulled out of the sandbox)
    # for claude_max mode.
    messages: list[dict[str, Any]] | None
    session_id: str | None
    session_blob: bytes | None


class SkillsDeltaModel(BaseModel):
    """A skills delta triage may emit: ``remove`` first, then ``add`` (spec 3.3)."""

    model_config = ConfigDict(extra="forbid")

    add: list[str] = Field(default_factory=list)
    remove: list[str] | Literal["*"] = Field(default_factory=list)


class PipelineConfigModel(BaseModel):
    """Strict Pydantic equivalent of the PipelineConfig TypedDict.

    Used only at the triage-parse boundary; downstream code continues to
    interact with the dict form stored in JobState (LangGraph requires dict).
    """

    model_config = ConfigDict(extra="forbid")

    # Empty means triage did not name a profile. The sandbox is created from
    # the job or the repo preference before triage runs, and there is no
    # profile named "default" — that word used to be filled in here and then
    # echoed back by the model as if it had chosen one.
    sandbox_profile: str = ""
    max_feedback_loops: int = Field(default=5, ge=0, le=20)
    reviewer_enabled: bool = True
    validator_modes: list[str] = Field(default_factory=list)
    agent_models: dict[str, str] = Field(default_factory=dict)
    agent_tools: dict[str, list[str]] = Field(default_factory=dict)
    agent_skills: dict[str, list[str] | SkillsDeltaModel] = Field(default_factory=dict)
    budget_usd: float = Field(default=10.0, ge=0.0)
    execution_modes: dict[str, str] = Field(default_factory=dict)
    auth_modes: dict[str, str] = Field(default_factory=dict)
    system_prompts: dict[str, str] = Field(default_factory=dict)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    # EMB-35: per-agent turn budgets (extra="forbid" would reject the key
    # if triage emitted it without this declaration).
    agent_max_turns: dict[str, int] = Field(default_factory=dict)


class TriageDecisionModel(BaseModel):
    """Strict Pydantic equivalent of the TriageDecision TypedDict."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(proceed|needs_info|split)$")
    confidence: float = Field(ge=0.0, le=1.0)
    # RAV-601: closed vocabulary — the enum type IS the parse-boundary
    # validation the owner decided on.
    job_kind: JobKind = JobKind.CODE
    # Empty = "use the default_for_kind template" (plan_route_node also
    # treats the legacy free-form values "standard"/"routine" as empty).
    pipeline_template: str = ""
    pipeline_config: PipelineConfigModel = Field(default_factory=PipelineConfigModel)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    sub_tasks: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str = ""
    # Phase 5: triage emits this inline in its JSON when the issue→PR job
    # should be QA-validated post-review. Mirrors SetQADecision in
    # embry0.agents.triage_actions; we duplicate the shape here to keep
    # state.py free of agent-layer imports. triage_node copies these fields
    # to state["qa"]["needs_qa"] / qa_required_reason / acceptance_criteria.
    set_qa_decision: dict[str, Any] | None = None
    # Phase 5 Task 7: when triage is re-invoked after a QA failure, it embeds
    # one of three actions here: {"kind": "retry_developer"|"rerun_qa"|"ask_user",
    # ...}. triage_node validates the kind-specific shape via the
    # RetryDeveloper / RerunQA / AskUser models in embry0.agents.triage_actions
    # and routes the workflow accordingly. Stored as a loose dict here to keep
    # state.py free of agent-layer imports and to allow extra="forbid" on the
    # outer model without rejecting the new key.
    qa_failure_action: dict[str, Any] | None = None
    # Slop dungeon (spec 2026-10-25): the slopometer's verdict, loose here and
    # parsed leniently by plan_route (embry0.services.slop.parse_work_nature):
    # a malformed verdict is dropped, never job-terminal.
    work_nature: Any = None
    # RAV-1079: optional duplicate-PR flag. Only meaningful when the user
    # turn carried an "Existing OPEN pull requests" list; triage_node
    # validates pr_number against that list (state["open_prs"]) and ends the
    # job as skipped_duplicate linking the existing PR (skip+link policy —
    # an unmatched number is ignored, never a failure). Loose dict for the
    # same layering reason as set_qa_decision.
    duplicate_of_pr: dict[str, Any] | None = None


class TriageParseError(ValueError):
    """Raised when LLM output can't be parsed into a TriageDecisionModel.

    Callers should mark the job failed with ErrorCode.TRIAGE_MALFORMED and
    log the raw LLM output for debugging.
    """


class UnsupportedContextError(Exception):
    """Raised when a job's context type has no workspace-init strategy.

    All four builtin context types (git/http/local/none) ship strategies in
    embry0/workspace_init/ (RAV-600); this now fires only for unknown types
    with no registered entry point. Raised before any sandbox is created;
    issue_executor maps it to ErrorCode.UNSUPPORTED_CONTEXT.
    """

    def __init__(self, context_type: str) -> None:
        self.context_type = context_type
        super().__init__(f"Context type {context_type!r} has no workspace-init strategy registered")


# ---- reducers (RAV-1142) ----------------------------------------------------
#
# LangGraph raises INVALID_CONCURRENT_GRAPH_UPDATE — killing the whole job —
# when a plain (non-Annotated) key receives more than one write in a single
# superstep. Re-entered stages do exactly that: both watcher-dispatched jobs
# of 2026-07-30 died this way (`current_stage` on the double-resume path,
# `qa` on the qa_rerun path) AFTER their PRs were already pushed.
#
# Policy (#304 closed the class): NO JobState key may be a plain key. Which
# keys can collide is not statically knowable — node updates are built from
# spreads and helper returns, and on the double-resume path the SAME node
# runs twice, so even a single-writer key collides. Every key therefore
# carries either one of the value-merging reducers below (counters, the qa
# block, the append lists) or a channel from orchestration/channels.py:
#   FoldingLastValue — last-write-wins (the default; what the code assumed)
#   PreferNonEmpty   — pr_url, sandbox_container_id: a concurrent None must
#                      not erase a sibling's value
#   UnionOfLists     — pending_agent_questions: concurrent askers' questions
#                      are all kept; a dropped question is never asked
# Every fold logs state_channel_folded_concurrent_writes at WARNING (no graph
# fans out today, so it always marks a routing bug). The function reducers
# below cannot tell a concurrent write from a sequential one and stay silent.
# For single writes every one of them is identical to plain assignment, so
# sequential semantics are unchanged; they only change what happens in the
# superstep that previously crashed. tests/unit/orchestration/
# test_state_channels.py fails when a new key is added without one.


def last_value(current: Any, update: Any) -> Any:
    """Fold concurrent writes to the latest update (stage labels, scalars)."""
    return update


def max_value(current: Any, update: Any) -> Any:
    """Fold monotonic counters/totals to the larger value.

    Concurrent writers each computed old+delta from the same snapshot; max
    keeps the larger — possibly undercounting one delta, never crashing and
    never regressing backwards.
    """
    if current is None:
        return update
    if update is None:
        return current
    return max(current, update)


def merge_qa_block(current: Any, update: Any) -> Any:
    """Shallow-merge concurrent QA-block writes; an explicit None clears.

    Nodes build their qa write from the current block and replace it whole,
    so a sequential merge is equivalent to the old replace. Concurrent
    writers each contribute their keys instead of one clobbering the other.
    (Consequence: a node cannot REMOVE a key by omitting it — none does;
    overwrite with None inside the block to clear a field.)
    """
    if update is None or current is None:
        return update
    if isinstance(current, dict) and isinstance(update, dict):
        return {**current, **update}
    return update


class JobState(TypedDict, total=False):
    job_id: Annotated[str, FoldingLastValue]
    repo: Annotated[str, FoldingLastValue]
    context: Annotated[
        dict[str, Any] | None, FoldingLastValue
    ]  # typed job context; None → treat as git for back-compat
    task: Annotated[str, FoldingLastValue]
    issue_number: Annotated[int | None, FoldingLastValue]
    # The forge-native issue number (GitHub number or GitLab iid), None for
    # a Linear issue. issue_number stays GitHub-only for its GitHub-shaped
    # readers; the QA report reads this one to write on either forge (#65).
    forge_issue_number: Annotated[int | None, FoldingLastValue]
    issue_id: Annotated[str | None, FoldingLastValue]
    # EMB-39: labels from the originating issue (empty for standalone QA
    # jobs). Feeds conditional-criteria `labels:` predicates in the QA
    # orchestrator. Top-level key — MUST stay declared here or LangGraph's
    # state-merge reducer silently drops it (see repo_root note below).
    issue_labels: Annotated[list[str], FoldingLastValue]
    # RAV-1079: open PRs on the target repo, fetched by IssueExecutor before
    # the graph starts ({number, title, head_ref, url, author, issue_refs,
    # closes_numbers, issue_prefix} dicts from services/github_prs.py). Empty/
    # absent for jobs that already target an existing PR (pr-followup) and on
    # fetch failure (fail-open). Read-only context for triage/developer/review
    # prompt injection + triage's duplicate_of_pr validation. MUST stay
    # declared here — LangGraph's merge reducer silently drops undeclared keys.
    open_prs: Annotated[list[dict[str, Any]], FoldingLastValue]
    sandbox_container_id: Annotated[str | None, PreferNonEmpty]
    pipeline_config: Annotated[
        PipelineConfig, FoldingLastValue
    ]  # always the flat inner PipelineConfig dict (never TriageDecision)
    triage_decision: Annotated[
        TriageDecision, FoldingLastValue
    ]  # full triage output including action/reasoning/questions
    # RAV-1058: per-job overrides from JobCreateRequest, surfaced on state by
    # IssueExecutor._run_workflow. MUST stay declared here — LangGraph's
    # state-merge reducer silently drops undeclared keys (see repo_root note
    # below). sandbox_profile is resolved by init_node; max_budget_usd is
    # folded over triage's pipeline_config.budget_usd in triage_node;
    # pipeline_template_request outranks triage's pick in plan_route_node;
    # the mode overrides are read by run_agent_node. budget_overrun_mode_override
    # (#275) only ever tightens: "hard" is honoured, anything else is ignored.
    # It is written once, in the executor's initial state, and by no node, so
    # its fold (#304) is a formality: last-write-wins, and the worst a
    # colliding non-"hard" write could do is leave the job on the deployment's
    # default mode — never a looser one.
    sandbox_profile: Annotated[str | None, FoldingLastValue]
    max_budget_usd: Annotated[float | None, FoldingLastValue]
    budget_overrun_mode_override: Annotated[str | None, FoldingLastValue]
    pipeline_template_request: Annotated[str | None, FoldingLastValue]
    # #306: True when the job works a healing case the watcher opened. Set
    # once by the executor; plan_route_node then refuses triage's lighter
    # template pick so the job always runs review and verification. MUST stay
    # declared here for the same reason as the keys above.
    pin_full_pipeline: Annotated[bool | None, FoldingLastValue]
    execution_mode_override: Annotated[str | None, FoldingLastValue]
    auth_mode_override: Annotated[str | None, FoldingLastValue]
    # ADR 0022: a context audit's job opens a pull request from the patch its
    # report carries. Set once by the executor from the job's pipeline_config;
    # read by finalize_output_node. MUST stay declared here.
    open_pr: Annotated[bool | None, FoldingLastValue]
    # The title and closing note of a pull request the executor opens from a
    # branch a step other than the developer pushed (the context audit's);
    # absent, the executor's defaults for a developer's branch.
    pr_title: Annotated[str | None, FoldingLastValue]
    pr_note: Annotated[str | None, FoldingLastValue]
    # RAV-1254: per-repo pre-PR self-checks from repo_preferences.dev_checks
    # ([{name, paths, command}]), seeded by _run_workflow and rendered into
    # the developer prompt. MUST stay declared here — LangGraph's state-merge
    # reducer silently drops undeclared keys.
    dev_checks: Annotated[list[dict[str, Any]], FoldingLastValue]
    global_context: Annotated[str | None, FoldingLastValue]
    repo_context: Annotated[str | None, FoldingLastValue]
    additional_context: Annotated[str | None, FoldingLastValue]
    auth_proxy_url: Annotated[str | None, FoldingLastValue]
    git_proxy_url: Annotated[str | None, FoldingLastValue]
    agent_outputs: Annotated[list[AgentOutputEntry], operator.add]
    errors: Annotated[list[str], operator.add]
    # RAV-1142: every node writes current_stage (43 write-sites) — the first
    # concurrent superstep crashed the job. Progress label → last write wins.
    current_stage: Annotated[str, last_value]
    # RAV-1281: error_code is written by many terminal exits (triage
    # malformed/unresolved, qa_exhausted, dev-branch-missing, ask-user cap,
    # empty interrupt…) but was never DECLARED here, so LangGraph silently
    # dropped it from the merged channel state — it only reached the
    # executor via _process_stream's raw updates-stream merge. Declared so
    # checkpoint state / aget_state carry it too. Same reducer rationale as
    # current_stage: single-dispatch writers, last write wins.
    error_code: Annotated[str | None, last_value]
    validation_result: Annotated[dict[str, Any] | None, last_value]
    feedback_context: Annotated[str | None, last_value]
    retry_count: Annotated[int, max_value]
    total_cost_usd: Annotated[float, max_value]
    budget_overrun_usd: Annotated[float, max_value]
    branch_name: Annotated[str | None, FoldingLastValue]
    # base_branch is the repo's integration branch: the ref the clone fetches,
    # the orchestrator's git-diff bases on, and the MR/PR targets. Resolved
    # once by init_node (embry0/services/base_branch.py) with precedence
    # job override (QAJobOverrides.base_branch on POST /api/v1/jobs) →
    # repo_preferences.base_branch → the forge project's default branch →
    # "main". Was previously set ONLY by a QA job override, which is why the
    # clone hardcoded main and the review diff had to re-derive it.
    base_branch: Annotated[str | None, FoldingLastValue]
    # Which step of the resolver answered: "override", "preference", "forge"
    # or "fallback" (embry0.services.base_branch.FALLBACK_SOURCE). "main,
    # because nobody knew" and "main, because the project says so" are the
    # same string but not the same fact, and the review node has a cheaper,
    # network-free answer of its own — the clone's origin/HEAD symref. Without
    # this label it could not tell that base_branch was a guess, so it stopped
    # doing the symref lookup and silently undid RAV-1405. MUST stay declared:
    # LangGraph's merge reducer drops undeclared keys.
    base_branch_source: Annotated[str | None, FoldingLastValue]
    # Phase 3 (post-deploy fix): repo_root is the local-disk path the
    # workspace_provider reads from. init_orchestrator_node stages workspace
    # metadata from the bootstrap sandbox into /tmp/embry0-workspace-<job_id>
    # and writes that path here so qa_orchestrator_node can hand it to
    # NpmWorkspacesTurboProvider. Must be declared on JobState — LangGraph's
    # state-merge reducer silently drops keys not present in the schema, so
    # the C2 patch's `out["repo_root"] = staging_path_str` was a no-op until
    # this declaration was added.
    repo_root: Annotated[str | None, FoldingLastValue]
    pr_url: Annotated[str | None, PreferNonEmpty]
    # #488: the developer's own description of its change (the "summary" of
    # its final JSON), the body of the PR the orchestrator opens for it. A
    # fact of its own because agent_outputs is a reducer channel: the merged
    # result the PR fallback reads holds only the last node's fragment. MUST
    # stay declared — LangGraph's merge reducer drops undeclared keys.
    developer_summary: Annotated[str | None, FoldingLastValue]
    # Salvage (spec 2026-10-25): {branch, agent, reason, committed} when a
    # coding run stopped before it pushed and embry0 committed and pushed what
    # it left, unfinished. The next coding attempt is told to finish it; a run
    # that read it clears it. MUST stay declared (the merge reducer drops
    # undeclared keys).
    unfinished_work: Annotated[dict[str, Any] | None, FoldingLastValue]
    result_summary: Annotated[str | None, FoldingLastValue]
    # RAV-603: typed deliverables collected by finalize_output_node
    # ({type, title, content, url, storage_bucket, storage_key, media_type,
    # size_bytes, metadata} dicts). Persisted as deliverables rows by
    # IssueExecutor._handle_workflow_result. MUST stay declared here —
    # LangGraph's merge reducer silently drops undeclared keys.
    deliverables: Annotated[list[dict[str, Any]], FoldingLastValue]
    # RAV-601 template routing. MUST stay declared here — LangGraph's merge
    # reducer silently drops undeclared keys (see repo_root note above).
    # job_kind mirrors jobs.job_kind; route_plan is the linearized template
    # snapshot (a mid-run template edit cannot re-route a planned job);
    # route_cursor indexes the NEXT step to execute.
    # RAV-1271: route_cursor uses last_value, NOT max_value — re-entry paths
    # (QA-failure retry_developer) intentionally point the cursor BACK at an
    # earlier step via cursor_at(), and max_value silently discarded those
    # backward writes (so a post-QA retry could skip review or end the job
    # early). last_value still folds concurrent superstep writes without
    # crashing; single-dispatch Command routing (RAV-1152) means sequential
    # semantics are plain assignment, which is what the routing code assumes.
    job_kind: Annotated[str, FoldingLastValue]
    # Slop dungeon (spec 2026-10-25): triage's slopometer verdict
    # (embry0.services.slop.parse_work_nature), the agent the route's coding
    # step runs as ("developer" or "slopmaster"), and a cleanup's diff counts.
    # Set by plan_route and the developer node. MUST stay declared here —
    # LangGraph's merge reducer silently drops undeclared keys.
    work_nature: Annotated[dict[str, Any] | None, FoldingLastValue]
    coding_agent: Annotated[str | None, FoldingLastValue]
    route_plan: Annotated[list[dict[str, Any]], FoldingLastValue]
    route_cursor: Annotated[int, last_value]
    route_template_name: Annotated[str | None, FoldingLastValue]
    pending_agent_questions: Annotated[list[dict[str, Any]], UnionOfLists]
    # Agent ask-user events with importance="auto_answerable" carry a
    # suggested_answer that the orchestrator records as the answer instead of
    # pausing the workflow. Persisted as issue_inputs rows with
    # status='auto_answered'; user can override post-hoc from the dashboard.
    auto_answered_agent_questions: Annotated[list[dict[str, Any]], FoldingLastValue]
    user_answers: Annotated[Any, FoldingLastValue]
    # Job-wide counters for interrupt/resume cycle guards. Cap values live in
    # orchestration/nodes/agent.py (env-tunable, RAV-605).
    # agent_question_rounds: incremented each time any node (triage, developer,
    #   review) produces agent_ask_user events; capped by _enforce_ask_user_cap.
    # triage_question_rounds: incremented each time the triage interrupt loop
    #   resumes with a needs_info response; capped in triage_node.
    # user_retry_rounds: incremented each time the user clicks "continue" in
    #   max_retries_node; capped at USER_RETRY_ROUNDS_CAP.
    agent_question_rounds: Annotated[int, max_value]
    agent_questions_exhausted: Annotated[
        bool, FoldingLastValue
    ]  # set True when agent_question_rounds cap hit; routes to terminal failure
    triage_question_rounds: Annotated[int, max_value]  # cycle guard for triage interrupt/resume loops
    user_retry_rounds: Annotated[int, max_value]  # cycle guard for continue_retrying clicks in max_retries_node
    # EMB-35: one-shot copy of the user's continue_retrying guidance. The
    # developer delta prompt sends only this on a resumed session (the
    # accumulated additional_context lives in the resumed conversation
    # already); cleared by developer_node after consumption. Top-level key —
    # MUST stay declared here or LangGraph's state-merge reducer silently
    # drops it.
    user_retry_guidance: Annotated[str | None, FoldingLastValue]
    # EMB-35: changed-file set computed by review_node (git diff vs the base
    # branch inside the dev sandbox) to scope the review prompt. Top-level so
    # later phases can reuse it without re-diffing. Distinct from
    # state["qa"]["changed_files"], which the QA orchestrator computes in its
    # own bootstrap sandbox against the merged PR branch.
    changed_files: Annotated[list[str] | None, FoldingLastValue]
    # #298/#299: the verify node's result (VerificationResult dict: tier, gate
    # report, browser-QA plan, human_merge_only). Written whole by exactly one
    # node, so the fold is last-write-wins: a result is one atomic document
    # and merging two would describe a run that never happened. The durable
    # copy is the job_verifications row; this one feeds events and routing.
    verification: Annotated[dict[str, Any] | None, FoldingLastValue]
    # User-defined env vars merged into sandbox at init_node. List-of-dicts shape
    # (key/value/scope); scope is 'app' or 'qa'. The legacy plain-dict shape is
    # still accepted by _filter_user_env_for_sandbox for backwards compatibility.
    user_env_vars: Annotated[list[dict[str, str]] | dict[str, str], FoldingLastValue]
    # Per-job agent model override (JobCreateRequest.agent_models), surfaced on
    # state by IssueExecutor and read by run_agent_node. Top-level key — MUST
    # stay declared here or LangGraph's state-merge reducer silently drops it
    # (it did exactly that until this declaration was added).
    agent_models_override: Annotated[dict[str, str], FoldingLastValue]
    # Local models: the model on the operator's own server the job prefers for
    # every agent it names no model for, while a slot is free (the watcher's
    # analyses). From the job row's pipeline_config.local_first_model.
    local_first_model: Annotated[str | None, FoldingLastValue]
    # Set True by Phase 2's QA pipeline when running QA jobs; gates injection
    # of scope='qa' user env vars at the sandbox boundary. Defaults to False.
    qa_active: Annotated[bool, FoldingLastValue]
    # RAV-1140: set True by init_node ONLY when the job context carries the
    # watcher's watcher_log_access flag AND context-proxy provisioning (env +
    # enroll + token file) fully succeeded. generic_agent reads it to grant the
    # analysis agent the mcp__context__logs_query tool. Top-level key — MUST
    # stay declared here or LangGraph's state-merge silently drops it.
    watcher_log_tool: Annotated[bool, FoldingLastValue]
    # SH-P4.4: the logged failure a job is about — {source, case_id, signature,
    # samples, services}, redacted and bounded (workflows/issue_to_pr/evidence.py).
    # Written once by init_node and read by the developer and reviewer prompts;
    # PreferNonEmpty because losing it to a concurrent clear would silently
    # send the reviewer back to reviewing a diff without its purpose.
    case_evidence: Annotated[dict[str, Any] | None, PreferNonEmpty]
    # SH-P4.4: containers the context-proxy CONFIRMED this job's sandbox may
    # query (a subset of case_evidence.services). Set by init_node only on full
    # provisioning; the developer node grants mcp__context__logs_query when it
    # is non-empty. Same last-write semantics as watcher_log_tool above.
    case_log_containers: Annotated[list[str], FoldingLastValue]
    # Structured QA-specific state populated by Phase 2's QA pipeline nodes.
    # Present when pipeline=qa OR (Phase 5) triage sets needs_qa=True.
    # RAV-1142: six files write this block; the qa_rerun superstep produced
    # two writes at once and crashed the job — shallow-merge instead.
    qa: Annotated[QAStateBlock | None, merge_qa_block]
    # Phase 5 Task 7: triage→developer/QA/user routing fields populated by
    # ``_route_qa_failure_action`` when triage is re-invoked after a QA failure.
    # developer_prompt_addendum / developer_focus_files: consumed by developer_node
    # to inject the failure-summary guidance into its prompt (consumer wiring is a
    # follow-up — the routing currently sets these on state regardless).
    # qa_rerun_reason: recorded when triage opts to rerun the QA subpath
    # unchanged (e.g. flaky/environmental failure). No node reads it today.
    # (The ask_user action puts its question on pending_agent_questions in the
    # standard shape — the bespoke pending_user_question key that nothing read
    # was removed in RAV-1271.)
    developer_prompt_addendum: Annotated[str | None, FoldingLastValue]
    developer_focus_files: Annotated[list[str], FoldingLastValue]
    qa_rerun_reason: Annotated[str | None, FoldingLastValue]
    # EMB-50 onboard pipeline: analysis sandbox id, round counter, draft
    # qa.yaml text/parsed, smoke results, final_status. Owned entirely by
    # embry0/workflows/onboard/nodes.py; absent on other pipelines.
    onboard: Annotated[dict[str, Any] | None, FoldingLastValue]
    # EMB-50: skip the boot/ready smoke phase (onboard jobs, --no-smoke).
    skip_smoke: Annotated[bool, FoldingLastValue]
    # ADR 0019 propose mode (onboard jobs started with a setup proposal):
    # write_config stores nothing, and the runner keeps the draft on the
    # proposal for a person to apply.
    onboard_propose: Annotated[bool, FoldingLastValue]
    # #30 pr-review pipeline: the job's pipeline_config plus sandbox id,
    # checkout facts, previous review, verdict and final_status. Owned by
    # embry0/workflows/pr_review/nodes.py; absent on other pipelines.
    pr_review: Annotated[dict[str, Any] | None, FoldingLastValue]


class QAAttempt(TypedDict, total=False):
    """One attempt at running QA against a target."""

    attempt_n: int
    started_at: str  # ISO 8601
    ended_at: str | None
    sandbox_id: str | None
    qa_net_name: str | None
    artifact_prefix: str  # MinIO prefix like "<job_id>/<attempt_n>/"
    last_phase: Literal["boot", "seed", "e2e", "exploratory", "report"] | None
    exit_reason: str | None
    result_summary: dict[str, Any] | None
    log_artifact_url: str | None


class QAStateBlock(TypedDict, total=False):
    """All QA-specific state for a job. Lives at JobState['qa'] when
    pipeline=qa OR triage set needs_qa=True (Phase 5)."""

    needs_qa: bool  # Set by triage (Phase 5) on issue→PR jobs; read by post-review conditional edge to route into QA subgraph.
    qa_required_reason: (
        str | None
    )  # Triage's rationale for needs_qa value; written by triage, surfaced in audit/dashboard.
    qa_yaml_raw: str | None
    qa_yaml_parsed: dict[str, Any] | None
    sandbox_profile_name: str
    acceptance_criteria: list[str]
    attempts: list[QAAttempt]
    failure_rounds: int  # PR-flow triage↔QA cycles consumed; bumped when qa.report routes back to triage on failure (Phase 5), capped at max_qa_failure_rounds (default 2) — on exhaustion the job ends with ERR_QA_FAILURES_UNRESOLVED.
    final_status: Literal["pending", "passed", "failed", "exhausted", "skipped"]
    sandbox_token: str  # The QA sandbox's bearer for the presign proxy
    # Backend-owned boot phase (Plan: qa-boot-as-backend-node). Nothing
    # writes these on this state since the single-app boot node was removed
    # (#641); the sub-task graph's boot_app_node records boot in SubTaskState.
    boot_outcome: Literal["passed", "timeout", "startup_failed"] | None
    boot_duration_ms: int | None
    boot_attempts: int | None
    boot_diagnostic_screenshot_path: str | None  # MinIO key, e.g. "JOB1/1/screenshots/boot-timeout.png"
    # ── Multi-app additions (Phase 1) ──
    qa_yaml_v2_raw: str | None
    qa_yaml_v2_parsed: dict[str, Any] | None  # dict shape of QAYamlConfigV2.model_dump()
    apps_to_qa: list[str] | None
    per_app_results: list[dict[str, Any]] | None  # list of SubTaskResult.to_dict()
    outcome: dict[str, Any] | None  # OrchestratorOutcome dump
    validation_errors: list[str] | None
    validation_warnings: list[str] | None
    head_sha: str | None
    # Phase-C: when True, qa_orchestrator_node skips affected-set computation
    # and QAs every app declared under cfg.apps. Set via
    # QAJobOverrides.force_all_apps on the POST /api/v1/jobs request body.
    # cfg.qa_required == "always" produces the same effect (per-repo vs
    # per-job knob). Defaults to False.
    force_all_apps: bool
    # EMB-39: conditional-criteria group names forced ON for this run via
    # QAJobOverrides.force_conditional_groups (["*"] = all groups).
    force_conditional_groups: list[str]
    # Per-job browser-backend override via QAJobOverrides.browser_backend
    # (#229). When set, qa_orchestrator_node replaces every resolved app's
    # browser_backend for this one run. Absent/None = yaml/deployment
    # resolution stands.
    browser_backend: str | None
    # Per-job sandbox profile override via QAJobOverrides.sandbox_profile
    # (#537), applied by qa_orchestrator_node like browser_backend.
    sandbox_profile: str | None
    # The profile every sub-task sandbox resolved to, written by
    # qa_orchestrator_node for the jobs row; None when apps differ (#537).
    sandbox_profile_used: str | None
    # Structured failure reason for early orchestrator aborts (validation
    # errors, unknown forced conditional group, app-config resolution
    # failure). Surfaced verbatim on the jobs row by issue_executor when the
    # run fails before any per-app breakdown exists.
    error_message: str | None
    # Repo-relative changed files from init_orchestrator_node's git diff.
    # Was previously written by key without a declaration (survived only
    # because nodes replace the whole qa dict); declared for typing hygiene.
    changed_files: list[str] | None
    # #298: set on every QA run inside the issue-to-PR graph. A branch job
    # never QAs a `target: deployed` app — the deployed site does not contain
    # the branch. Standalone QA jobs never set it.
    exclude_deployed_apps: bool

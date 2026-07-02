"""Base state schemas for LangGraph orchestration."""

from __future__ import annotations

import operator
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field


class TriageAction(StrEnum):
    PROCEED = "proceed"
    NEEDS_INFO = "needs_info"
    SPLIT = "split"


class AskUserChannel(StrEnum):
    """Outbound channels for agent ask-user questions.

    Per-issue list stored in ``issues.notification_channels`` (jsonb). When an
    agent calls ``athanor.sandbox.ask_user``, the dispatcher fans out the
    question to every channel selected on the originating issue. Used as the
    typed validation set on the API request side; responses serialize as
    plain strings so dashboard clients don't have to import the enum.
    """

    DASHBOARD = "dashboard"
    TELEGRAM = "telegram"
    GITHUB = "github"


class PipelineConfig(TypedDict, total=False):
    sandbox_profile: str
    max_feedback_loops: int
    reviewer_enabled: bool
    validator_modes: list[str]
    agent_models: dict[str, str]
    agent_tools: dict[str, list[str]]
    agent_skills: dict[str, list[str]]
    budget_usd: float
    # Phase 1 additions — per-agent-type overrides produced by triage.
    execution_modes: dict[str, str]
    auth_modes: dict[str, str]
    system_prompts: dict[str, str]
    mcp_servers: dict[str, dict[str, Any]]


_PIPELINE_DEFAULTS: dict[str, Any] = {
    "max_feedback_loops": 3,
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
    pipeline_template: str
    pipeline_config: PipelineConfig
    questions: list[Any]  # list of strings or dicts with question/importance/suggested_answer
    sub_tasks: list[dict[str, Any]]
    reasoning: str


class AgentOutputEntry(TypedDict, total=False):
    agent_type: str
    is_error: bool
    error_message: str
    output: str
    cost_usd: float
    duration_ms: int
    tools_called: dict[str, int]
    # Plan C: post-run conversation state forwarded by run_agent_node so
    # workflow nodes (developer/triage/review) can persist via
    # AgentSessionsRepository. ``messages`` for api_key mode;
    # ``session_id`` + ``session_blob`` (bytes pulled out of the sandbox)
    # for claude_max mode.
    messages: list[dict[str, Any]] | None
    session_id: str | None
    session_blob: bytes | None


class PipelineConfigModel(BaseModel):
    """Strict Pydantic equivalent of the PipelineConfig TypedDict.

    Used only at the triage-parse boundary; downstream code continues to
    interact with the dict form stored in JobState (LangGraph requires dict).
    """

    model_config = ConfigDict(extra="forbid")

    sandbox_profile: str = "default"
    max_feedback_loops: int = Field(default=3, ge=0, le=20)
    reviewer_enabled: bool = True
    validator_modes: list[str] = Field(default_factory=list)
    agent_models: dict[str, str] = Field(default_factory=dict)
    agent_tools: dict[str, list[str]] = Field(default_factory=dict)
    agent_skills: dict[str, list[str]] = Field(default_factory=dict)
    budget_usd: float = Field(default=10.0, ge=0.0)
    execution_modes: dict[str, str] = Field(default_factory=dict)
    auth_modes: dict[str, str] = Field(default_factory=dict)
    system_prompts: dict[str, str] = Field(default_factory=dict)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


class TriageDecisionModel(BaseModel):
    """Strict Pydantic equivalent of the TriageDecision TypedDict."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(proceed|needs_info|split)$")
    confidence: float = Field(ge=0.0, le=1.0)
    pipeline_template: str = "standard"
    pipeline_config: PipelineConfigModel = Field(default_factory=PipelineConfigModel)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    sub_tasks: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str = ""
    # Phase 5: triage emits this inline in its JSON when the issue→PR job
    # should be QA-validated post-review. Mirrors SetQADecision in
    # athanor.agents.triage_actions; we duplicate the shape here to keep
    # state.py free of agent-layer imports. triage_node copies these fields
    # to state["qa"]["needs_qa"] / qa_required_reason / acceptance_criteria.
    set_qa_decision: dict[str, Any] | None = None
    # Phase 5 Task 7: when triage is re-invoked after a QA failure, it embeds
    # one of three actions here: {"kind": "retry_developer"|"rerun_qa"|"ask_user",
    # ...}. triage_node validates the kind-specific shape via the
    # RetryDeveloper / RerunQA / AskUser models in athanor.agents.triage_actions
    # and routes the workflow accordingly. Stored as a loose dict here to keep
    # state.py free of agent-layer imports and to allow extra="forbid" on the
    # outer model without rejecting the new key.
    qa_failure_action: dict[str, Any] | None = None


class TriageParseError(ValueError):
    """Raised when LLM output can't be parsed into a TriageDecisionModel.

    Callers should mark the job failed with ErrorCode.TRIAGE_MALFORMED and
    log the raw LLM output for debugging.
    """


class UnsupportedContextError(Exception):
    """Raised when a job's context type has no init strategy yet (INT-599).

    Non-git contexts (http/local/none) validate and persist fine but are not
    executable until INT-600 (init strategies) + INT-601 (routing) land. The
    guard in init_node raises this before any sandbox is created; issue_executor
    maps it to ErrorCode.UNSUPPORTED_CONTEXT.
    """

    def __init__(self, context_type: str) -> None:
        self.context_type = context_type
        super().__init__(f"Context type {context_type!r} isn't executable yet — init/routing land in INT-600/INT-601")


class JobState(TypedDict, total=False):
    job_id: str
    repo: str
    context: dict[str, Any] | None  # INT-599 typed job context; None → treat as git for back-compat
    task: str
    issue_number: int | None
    issue_id: str | None
    sandbox_container_id: str | None
    pipeline_config: PipelineConfig  # always the flat inner PipelineConfig dict (never TriageDecision)
    triage_decision: TriageDecision  # full triage output including action/reasoning/questions
    global_context: str | None
    repo_context: str | None
    additional_context: str | None
    auth_proxy_url: str | None
    git_proxy_url: str | None
    athanor_proxy_url: str | None
    agent_outputs: Annotated[list[AgentOutputEntry], operator.add]
    errors: Annotated[list[str], operator.add]
    current_stage: str
    validation_result: dict[str, Any] | None
    feedback_context: str | None
    retry_count: int
    total_cost_usd: float
    budget_overrun_usd: float
    branch_name: str | None
    # Phase-C: base_branch is the ref the orchestrator's git-diff uses for
    # affected-set computation. None = fall back to "main" (existing
    # behavior). Set via QAJobOverrides.base_branch on the POST /api/v1/jobs
    # request body for repos whose default branch is master or develop.
    base_branch: str | None
    # Phase 3 (post-deploy fix): repo_root is the local-disk path the
    # workspace_provider reads from. init_orchestrator_node stages workspace
    # metadata from the bootstrap sandbox into /tmp/athanor-workspace-<job_id>
    # and writes that path here so qa_orchestrator_node can hand it to
    # NpmWorkspacesTurboProvider. Must be declared on JobState — LangGraph's
    # state-merge reducer silently drops keys not present in the schema, so
    # the C2 patch's `out["repo_root"] = staging_path_str` was a no-op until
    # this declaration was added.
    repo_root: str | None
    pr_url: str | None
    result_summary: str | None
    pending_agent_questions: list[dict[str, Any]]
    # Agent ask-user events with importance="auto_answerable" carry a
    # suggested_answer that the orchestrator records as the answer instead of
    # pausing the workflow. Persisted as issue_inputs rows with
    # status='auto_answered'; user can override post-hoc from the dashboard.
    auto_answered_agent_questions: list[dict[str, Any]]
    user_answers: Any
    # Job-wide counters for interrupt/resume cycle guards.
    # agent_question_rounds: incremented each time any node (triage, developer,
    #   review) produces agent_ask_user events; capped at 5 by _enforce_ask_user_cap.
    # triage_question_rounds: incremented each time the triage interrupt loop
    #   resumes with a needs_info response; capped at 5 in triage_node.
    # user_retry_rounds: incremented each time the user clicks "continue" in
    #   max_retries_node; capped at 3.
    agent_question_rounds: int
    agent_questions_exhausted: bool  # set True when agent_question_rounds cap hit; routes to terminal failure
    triage_question_rounds: int  # cycle guard for triage interrupt/resume loops, capped at 5
    user_retry_rounds: int  # cycle guard — capped at 3 continue_retrying clicks in max_retries_node
    # User-defined env vars merged into sandbox at init_node. List-of-dicts shape
    # (key/value/scope); scope is 'app' or 'qa'. The legacy plain-dict shape is
    # still accepted by _filter_user_env_for_sandbox for backwards compatibility.
    user_env_vars: list[dict[str, str]] | dict[str, str]
    # Set True by Phase 2's QA pipeline when running QA jobs; gates injection
    # of scope='qa' user env vars at the sandbox boundary. Defaults to False.
    qa_active: bool
    # Structured QA-specific state populated by Phase 2's QA pipeline nodes.
    # Present when pipeline=qa OR (Phase 5) triage sets needs_qa=True.
    qa: QAStateBlock | None
    # Phase 5 Task 7: triage→developer/QA/user routing fields populated by
    # ``_route_qa_failure_action`` when triage is re-invoked after a QA failure.
    # developer_prompt_addendum / developer_focus_files: consumed by developer_node
    # to inject the failure-summary guidance into its prompt (consumer wiring is a
    # follow-up — the routing currently sets these on state regardless).
    # qa_rerun_reason: consumed by init_qa_node when triage opts to rerun the QA
    # subpath unchanged (e.g. flaky/environmental failure).
    # pending_user_question: consumed by ask_user_interrupt to surface a
    # triage-originated escalation question to the user.
    developer_prompt_addendum: str | None
    developer_focus_files: list[str]
    qa_rerun_reason: str | None
    pending_user_question: str | None


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
    sandbox_token: str  # Set by init_qa, consumed by report
    # Backend-owned boot phase (Plan: qa-boot-as-backend-node).
    # boot_qa_node populates these; the agent (qa_node) reads them but does
    # NOT mutate them.
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

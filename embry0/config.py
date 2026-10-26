"""Application configuration via Pydantic BaseSettings."""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal, NamedTuple

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from embry0.config_redaction import NOT_SECRET, SecretSetting, redact_setting
from embry0.trackers.author_association import DEFAULT_AUTHOR_ROLES


class TelegramSenderAllowlist(NamedTuple):
    """Parsed ``TELEGRAM_ALLOWED_SENDER_IDS``: admitted ids plus rejected entries."""

    ids: frozenset[int]
    invalid: tuple[str, ...]


class Embry0Config(BaseSettings):
    """All runtime configuration for the embry0 orchestrator.

    #535: a credential is typed ``SecretSetting`` (still a ``str`` to every
    reader), so ``embry0 config``, ``repr`` and ``str`` mask it. A field whose
    name looks secret (``SECRET_NAME_PATTERN``) is masked even untyped, and a
    unit test fails until it is classified: either ``SecretSetting``, or
    ``Annotated[<type>, NOT_SECRET]`` when the name only looks secret. See
    ``embry0/config_redaction.py``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __repr_args__(self) -> Iterator[tuple[str | None, Any]]:
        """``repr``/``str`` show what ``embry0 config`` shows: secrets and URL
        passwords masked. structlog renders a config object through this."""
        model = type(self)
        for name, value in super().__repr_args__():
            yield name, value if name is None else redact_setting(model, name, value)

    # Database (PostgreSQL)
    database_url: str = "postgresql://embry0:embry0@localhost:5432/embry0"

    # GitHub integration
    github_token: SecretSetting = ""
    github_webhook_secret: SecretSetting = ""

    # Alertmanager as a detection source (#296, SH-P3.8). An empty secret makes
    # the receiver answer 503 outside dev mode — the same posture as every
    # other webhook here. Alertmanager cannot HMAC a body, so it presents the
    # secret itself as `Authorization: Bearer <secret>`; the comparison is
    # still constant-time.
    alertmanager_webhook_secret: SecretSetting = ""
    # JSON object mapping a label VALUE to a repo, e.g.
    # {"backend": "acme/backend"}. Consulted when an alert
    # carries no explicit `repo` label; keys are matched against the alert's
    # repo / service / container_name / name / job labels, in that order. An
    # unparseable value maps nothing and logs — a typo here must not stop the
    # orchestrator, and a receiver that maps nothing answers "ignored" with the
    # reason rather than 500-ing at Alertmanager.
    alertmanager_repo_map: str = ""

    # Linear integration (EMB-47). All empty = integration off (the webhook
    # route answers "not configured"). Repo routing is stamp-first (RAV-1397):
    # the issue's project carries `repo: owner/name` in its summary (fallback:
    # project document). linear_repo_map is the static FALLBACK behind the
    # stamp — a JSON object mapping Linear project name (or team key) → GitHub
    # owner/repo, e.g. {"Acme Platform": "acme/backend"}.
    linear_api_key: SecretSetting = ""
    linear_webhook_secret: SecretSetting = ""
    linear_repo_map: str = ""
    # Dispatch reconciler (#284): every interval, list the dispatchable Linear
    # tickets carrying the trigger label and dispatch the ones a lost webhook
    # left without a job. It idles (no API calls) while Linear has no API key.
    #   mode: off | shadow | on. shadow (the default) takes every decision and
    #   changes nothing — it logs linear_reconcile_would_dispatch / would_skip
    #   once per ticket. A human promotes it to `on`.
    #   interval 0 also disables the loop.
    # Bounds, all counted from durable state: jobs per sweep, jobs per rolling
    # 24 h, jobs the reconciler may ever start for one ticket, and dispatches
    # that raised before a job existed (then exponential backoff, then stop).
    # stale_triaging_minutes: reopen Linear rows stuck in `triaging` with no
    # live job for this long (0 disables).
    linear_reconcile_mode: Literal["off", "shadow", "on"] = "shadow"
    linear_reconcile_interval_seconds: int = 300
    # #335: the first sweep after a start waits this long, not a whole interval.
    linear_reconcile_startup_delay_seconds: int = 90
    linear_reconcile_max_dispatches: int = 3
    linear_reconcile_max_dispatches_per_day: int = 5
    linear_reconcile_max_attempts_per_ticket: int = 2
    linear_reconcile_failure_max_attempts: int = 5
    linear_reconcile_stale_triaging_minutes: int = 15

    # Deterministic verification after an approved review (#298, #299):
    # on | shadow | off. Ships in `shadow`: it takes every decision and
    # records it, but changes no routing and writes nothing to a forge. `on`
    # additionally fails a job on a NEW test failure, prunes browser QA by
    # tier, and posts the verdict + `human-merge-only` label on the PR — an
    # automated write under a human's identity, so a human promotes it.
    # `off` skips the node. Note the deployed-target QA fix is structural
    # (the branch QA node) and is NOT gated by this setting.
    # A plain str on purpose: an unrecognised value is logged and treated as
    # `shadow`, never a startup failure.
    verification_mode: str = "shadow"

    # Merge sync (#279): every interval, ask GitHub (read-only) what became of
    # the PRs embry0 jobs produced, and record it — it covers missed webhook
    # deliveries and repos with no webhook at all. It dispatches nothing and
    # writes nothing to a forge, but it DOES write here: jobs.delivery_state,
    # jobs.status, jobs.pr_url, the embry0 issue of a merged PR, and ledger
    # rows. So it ships in `shadow` like every other unattended writer: one
    # sweep's worth of merge_sync_would_record tells you what it would do
    # before it does any of it.
    #   mode: off | shadow | on. A value outside the three is logged CRITICAL
    #   and treated as `off` — a typo here must not stop the orchestrator.
    #   interval 0 also disables the loop. NOTE the mode gates only the SWEEP:
    #   the pull_request webhook records through the same code whatever the
    #   mode says, because a delivery is a fact that arrived, not an action
    #   embry0 chose to take.
    #   max_requests: GitHub API calls one sweep may make (it resumes where it
    #   stopped). max_writes: job rows one sweep may change before it stops
    #   and leaves the rest to the next one. rate_limit_floor: stop the sweep
    #   once GitHub reports fewer calls than this left on the token.
    #   list_pages / unlinked_lookback_days bound the search for PRs opened
    #   from a job's branch by someone else.
    merge_sync_mode: str = "shadow"
    merge_sync_interval_seconds: int = 900
    merge_sync_max_requests: int = 60
    merge_sync_max_writes: int = 200
    merge_sync_rate_limit_floor: int = 500
    merge_sync_list_pages: int = 1
    merge_sync_unlinked_lookback_days: int = 30
    # #527: GitHub repos this deployment renamed, as a JSON object mapping the
    # old owner/repo to the current one, e.g. {"acme/old": "acme/new"}. GitHub
    # redirects an old URL, so it still opens, but it no longer EQUALS the URL
    # the forge reports for the same PR; merge sync and the healing ledger
    # match a stored URL under either slug. Empty = no renames. Installed at
    # startup (embry0/storage/repo_renames.py). A value or entry that does not
    # parse is logged as repo_renames_invalid and skipped: a typo here must
    # not stop the orchestrator.
    repo_renames: str = ""

    # GitLab (#164). All empty = integration off; the webhook route answers
    # "not configured". gitlab_url covers self-managed instances — there is no
    # fixed API origin the way there is for github.com. gitlab_repo_map is a
    # JSON object mapping GitLab project path → target repo, e.g.
    # {"acme/widgets": "acme/widgets"}; an unmapped
    # project is ignored even when the issue carries the trigger label.
    gitlab_url: str = "https://gitlab.com"
    gitlab_token: SecretSetting = ""
    gitlab_webhook_secret: SecretSetting = ""
    gitlab_repo_map: str = ""
    # Spec 1A §3.2: path (inside the orchestrator container) to a PEM bundle
    # for a self-managed instance behind a private CA. Read once at startup;
    # an unreadable or non-certificate path refuses the boot. Empty is the
    # normal case — gitlab.com is signed by a public CA.
    gitlab_ca_bundle: str = ""

    # Spec 1A §3.8: the forge assumed for a repo that was never registered.
    # "github" keeps every pre-#162 deployment behaving exactly as before.
    # Set to "" on a tenant that must never fall back to github.com — an
    # unregistered repo then fails the job with ERR_REPO_UNREGISTERED at
    # dispatch instead of cloning from the wrong host. (A tracker or issue
    # dispatch refuses an unregistered repo whatever this says, #360.)
    default_forge: str = "github"

    # Watcher/proposer (RAV-657, W1d): scheduled log-scan → analysis job →
    # DRAFT Linear ticket → human ping. Disabled by default; the human gate
    # is structural — the watcher never applies the `embry0` trigger label,
    # a human does that on the draft ticket to accept the proposal.
    watcher_enabled: bool = False
    watcher_interval_seconds: int = 3600
    # #335: the first tick after a start is scheduled from the last RECORDED
    # tick, not from process start. An overdue tick waits this long first, so
    # a deploy is not competing with an analysis job and the startup LogQL
    # probe has a verdict; a tick that is not due yet waits out its interval.
    watcher_startup_delay_seconds: int = 90
    # No code default — where Loki lives is deployment-specific, same as
    # watcher_logql below. Empty stays empty; this is not a startup error.
    watcher_loki_url: str = ""
    # #270: no code default — which stack to watch is deployment-specific, and
    # a default that matches nothing fails silent. Needed when watcher_enabled,
    # but deliberately NOT a startup error: the watcher is optional, and a
    # config that refuses to load crash-loops the whole orchestrator. Unset =
    # the loop does not tick, logs CRITICAL watcher_logql_missing and reports
    # missing_logql on /health/ready. Whether a set value PARSES is Loki's
    # call, probed at watcher startup (services/logql_check.py). Write it in
    # .env with plain double quotes — escaped quotes reach Loki verbatim.
    watcher_logql: str = ""
    watcher_min_log_lines: int = 5  # fewer matching lines than this → skip the tick
    watcher_max_log_lines: int = 200  # excerpt cap fed to the analysis agent
    # #283: counted on the healing-case ledger (open, unaccepted watcher drafts).
    # A full cap defers FILING only — the tick still analyses its window.
    watcher_max_open_proposals: int = 3
    # #283: findings that may wait for a draft slot. With the cap and this queue
    # both full a proposal could be neither filed nor kept, so the (paid)
    # analysis is not run: the tick is `skipped_backlog` with its line count.
    watcher_max_deferred_findings: int = 3
    # #283: analyses per rolling 24h that may be paid for WHILE THE DRAFT CAP
    # IS FULL. Before #283 such a tick skipped before Loki was queried, so the
    # draft cap was also the spend cap; a full-cap stretch now costs up to this
    # many analyses a day (times the per-job hard cap of WATCHER_JOB_BUDGET_USD)
    # instead of nothing. Ordinary ticks are not counted and never blocked.
    # Counted durably from watcher_runs.analysed_under_full_cap, so a restart
    # does not refill the budget.
    watcher_max_full_cap_analyses_per_day: int = 6
    # RAV-1073: proposals/fingerprints older than this age out of the open-proposal
    # cap and the fingerprint dedup, so a cancelled-in-Linear draft (invisible to us)
    # stops pinning the cap forever and a long-fixed issue is re-filable. Shared with
    # the semantic dedup window (recent_proposal_titles).
    watcher_proposal_window_days: int = 7
    watcher_job_timeout_seconds: int = 1800
    # #275: per-job cap for an analysis job, always enforced as a hard stop
    # (embry0.services.watcher_budget) — ticks run unattended.
    watcher_job_budget_usd: float = 2.0
    # #289 (SH-P3.1) — deterministic log-signature clustering.
    #   off    : no clustering, no rows written (the pre-#289 behaviour)
    #   shadow : cluster, persist and log, but the prompt keeps the raw tail so
    #            the agent's own grouping stays in effect and the two can be
    #            compared over two weeks (the issue's fourth criterion)
    #   on     : the ranked cluster table replaces the raw tail in the prompt
    # Defaults to shadow, matching MERGE_SYNC_MODE / VERIFICATION_MODE: a new
    # detector observes before it decides anything.
    log_clustering_mode: str = "shadow"
    # SH-P3.3 (#291): skip the paid analysis when the window holds nothing new.
    #   off    : every tick with enough lines is analysed (pre-#291 behaviour)
    #   shadow : the decision is computed and logged as watcher_skip_shadow and
    #            the analysis runs anyway, so the ticks it WOULD have skipped
    #            can be checked against what they actually found
    #   on     : an unchanged window is recorded as `no_change`, with no job
    # Defaults to shadow for the same reason as LOG_CLUSTERING_MODE, only more
    # so: this switch decides NOT to look, and the cost of being wrong is a
    # missed fault rather than a differently-grouped prompt.
    watcher_skip_unchanged_mode: str = "shadow"
    # The novelty gate's memory, read under the same mode: a notable signature
    # an analysis found no issue in within these hours is held, unless its
    # volume grew past WATCHER_SPIKE_FACTOR times what that analysis read. The
    # tick is then `skipped_known`. 0 turns this part off.
    watcher_skip_known_hours: int = 24
    # Auto-accept of watcher drafts (spec 2026-10-25). Off by default: it
    # starts paid fix jobs with no person's yes. With it on, a tracked draft is
    # accepted, through the path POST /cases/{id}/accept takes, once it has
    # waited WATCHER_AUTO_ACCEPT_AFTER_HOURS (a person can cancel it first),
    # its severity is at least WATCHER_AUTO_ACCEPT_MIN_SEVERITY, its signature
    # was seen at least WATCHER_AUTO_ACCEPT_MIN_OCCURRENCES times, and fewer
    # than WATCHER_AUTO_ACCEPT_MAX_PER_DAY were auto-accepted in the last 24 h.
    watcher_auto_accept: bool = False
    watcher_auto_accept_after_hours: float = 24.0
    watcher_auto_accept_min_severity: str = "high"
    watcher_auto_accept_min_occurrences: int = 10
    watcher_auto_accept_max_per_day: int = 2
    # Thresholds for "is this signature interesting?", read by
    # embry0/services/signature_delta.py. Per-deployment today; #297 moves the
    # watcher's config into the DB per repo and these go with it.
    # A spike needs BOTH a ratio and an absolute floor: log volume is bursty,
    # and without the floor a signature going from one line to three reads as a
    # 3x emergency.
    watcher_spike_factor: float = 3.0
    watcher_spike_min_count: int = 10
    # How long a signature must be absent before its return counts as a
    # regression rather than a gap. Six hours is six missed hourly ticks.
    watcher_regression_silence_seconds: int = 21600
    # SH-P5.4 (#312): confirm-healed. ON by default, unlike every other
    # autonomy stage, and deliberately so: this one only reads the ledger and
    # comments on a ticket, so there is nothing it could do wrongly that a
    # shadow period would protect against. The plan says to build it early and
    # leave it on. Set false to silence it.
    confirm_healed_enabled: bool = True
    confirm_healed_interval_seconds: int = 1800
    confirm_healed_startup_delay_seconds: int = 120
    # One summary a day of what embry0 did (jobs, PRs, spend, watcher ticks,
    # healing cases, verdicts), sent as the `digest.daily` outbound webhook.
    # Replaces a notification per watcher analysis job. Sent once per UTC day,
    # at or after this hour; a process that starts later sends it at once.
    daily_digest_enabled: bool = True
    daily_digest_hour_utc: int = 14
    # Merge approvals (#319 phase 2): when a job opens a PR on one of these
    # repos, embry0 asks the operator's approval chat "merge this PR?" and a
    # reply relayed through the `merge_decide` tool merges it — after
    # re-checking the head sha, CI and the verification gate. `dry_run`
    # records the decision and reports "would merge" without writing to
    # GitHub; `on` squash-merges.
    # An empty chat id refuses every decision: it is the only chat whose
    # replies count, so there is no safe default. The repo list is empty by
    # default too — which repos an operator wants asked about is
    # configuration, and an empty list asks about nothing, so a deployment
    # that relies on the default silently stops asking; startup warns.
    merge_approval_mode: str = "dry_run"
    merge_approval_repos: str = ""
    merge_approval_chat_id: str = ""
    # The ask is posted once the PR's CI has settled, so its recommendation
    # reads real CI; after this long it is posted anyway, saying CI is still
    # running. Unanswered asks retire themselves: `closed` when the PR is
    # merged or closed on GitHub, `expired` after the expiry — nobody has to
    # reply "don't merge".
    merge_approval_ci_wait_minutes: int = 45
    merge_approval_expiry_days: int = 7
    merge_approval_sweep_seconds: int = 60
    # SH-P2.4: a tick's query window starts where the last analysed window
    # ended, so skipped/errored ticks widen the next one. This bounds that
    # widening — after a long outage the watcher looks back this far and no
    # further (never less than one interval); the run row's unanalysed_seconds
    # records what was left out. Windows are contiguous in TIME only: the
    # excerpt is still the newest watcher_max_log_lines matches, so a widened
    # window over the cap drops its oldest lines (log_lines_truncated on the
    # row). Quiet ticks do not advance the window either, so it grows each
    # quiet tick until it holds watcher_min_log_lines, then one analysis runs;
    # past the look-back it is a sliding window of this length. #289
    # (signature clustering) is what removes the line cap.
    watcher_max_lookback_seconds: int = 21600
    watcher_linear_team_id: str = ""  # required to file draft tickets
    watcher_linear_project_id: str = ""  # optional project for drafts
    # RAV-1139: change context for the analysis briefing. watcher_repo is the
    # GitHub repo whose commits correspond to the watched logs (empty = skip
    # the commits section). Each line the deploy-marker query matches is one
    # deploy, shown verbatim; watchtower "Session done ... Updated=N" lines
    # count only when N>0. #527: no code default — which lines mark a deploy
    # is deployment-specific, same as watcher_logql. Empty skips the briefing
    # section and the deploy ledger sync, is not probed, and is no fault.
    watcher_repo: str = ""
    watcher_deploy_logql: str = ""
    # RAV-1140 (watcher phase 2): context-proxy — mediated read-only log
    # drill-down for watcher-dispatched analysis jobs. The allowlist is the
    # exact container names of the watched repo's deployment (comma-separated);
    # empty = the proxy is not launched and watcher jobs run briefing-only.
    # The quota is per-sandbox for the sandbox's lifetime (kept under the
    # analysis agent's 40-turn cap, RAV-1055).
    context_proxy_enabled: bool = False
    context_proxy_container_allowlist: str = ""
    context_proxy_query_quota: int = 30
    # SH-P4.4: developer jobs whose ticket/healing case carries log evidence
    # can get the same log tool, scoped per sandbox to the allowlisted services
    # the evidence names, with redacted results (same proxy, same quota).
    # OFF by default: a code-writing agent reading production customer logs
    # acts without a human in the loop, so an operator opts in deliberately
    # (the evidence PROMPTS do not depend on this flag). It additionally needs
    # context_proxy_enabled and a non-empty allowlist.
    developer_log_tool_enabled: bool = False
    # #294: payload field names whose VALUES are personal data in the watched
    # stack's logs ("phone,customer_name"), comma-separated. Redacted, with
    # secrets and e-mail addresses, from every log line sent to a model
    # provider or filed in a tracker — the watcher's briefing, context-proxy
    # results, the evidence prompts, the watcher's draft tickets.
    log_redaction_fields: str = ""

    # RAV-1079: open-PR dedup on the developer/reviewer path. Before a fresh
    # pipeline job starts, the executor lists the target repo's open PRs:
    # an exact ticket match ends the job linking the existing PR (skip+link),
    # and the list is injected into the triage/developer/reviewer prompts for
    # semantic dedup. Fetch is fail-open — this flag is a kill-switch only.
    pr_dedup_enabled: bool = True
    pr_dedup_max_prs: int = Field(default=30, gt=0, le=100)

    # RAV-1092: comment-driven dispatch. A GitHub PR comment carrying the tag
    # (from an author whose association is in the allowed roles) dispatches a
    # pr-followup job on the PR's branch, adopting the PR-creating developer's
    # session so the comment lands as a delta turn. Off by default.
    comment_dispatch_enabled: bool = False
    comment_dispatch_tag: str = "@embry0"
    # Shared with the /answer comment gate (#253) — see trackers.author_association.
    comment_dispatch_author_roles: str = DEFAULT_AUTHOR_ROLES

    # API authentication
    api_key: SecretSetting = ""
    # Per-surface dev-mode flags. Each independently bypasses the corresponding
    # auth check. Both default to False (production posture).
    auth_dev_mode: bool = False
    webhook_dev_mode: bool = False
    allowed_cors_origins: str = ""
    trigger_labels: str = "embry0"

    # Rate limiting — defaults sized for trusted self-hosted operation
    # (RAV-605); tighten via env for anything internet-facing.
    # Fallback only, like max_budget_usd below: budget_config (DB row > env)
    # is what the dispatch gate reads. Kept equal to that repository's own
    # RATE_LIMIT_PER_AUTHOR_PER_HOUR fallback.
    rate_limit_per_author_per_hour: int = 5
    api_rate_limit_per_minute: int = 300
    # IP-keyed throttle for the unauthenticated webhook endpoints (RAV-1070).
    # Sized for GitHub's burstiest legitimate delivery patterns; the HMAC
    # check still rejects forgeries — this only bounds pre-verification work.
    webhook_rate_limit_per_minute: int = 120

    # Webhook request-body ceiling (bytes). Large monorepo push payloads can
    # exceed 1 MiB; 5 MiB keeps DoS exposure bounded on a LAN-only API.
    max_webhook_body_bytes: int = Field(default=5_242_880, gt=0)

    # Anthropic credentials. RAV-1239: the provider_mode/ollama_* fields
    # that used to sit here were config theater — no execution path ever
    # read them (auth_mode is resolved per repo/job/agent, non-Anthropic
    # providers route by model prefix via embry0/agents/providers.py).
    anthropic_api_key: SecretSetting = ""
    # Renamed from CLAUDE_MAX_OAUTH_TOKEN to CLAUDE_CODE_OAUTH_TOKEN (2026-04-28, Plan D)
    # to match auth_provider.py, sandbox, and CLAUDE.md documentation.
    claude_code_oauth_token: SecretSetting = ""
    default_model: str = ""
    model_heavy: str = "claude-opus-5"
    model_medium: str = "claude-sonnet-5"
    model_light: str = "claude-haiku-4-5"
    # Refuse to save an agent definition whose model routes to a provider
    # with no credential configured, or to no catalog provider at all. Off
    # by default: the save succeeds and the job fails when it runs, as
    # before. See embry0/services/provider_readiness.py, docs/providers.md.
    agent_model_provider_check: bool = False

    # Budget
    # Fallback only: budget_config (DB row > env) is the source of truth
    # for every budget setting. Kept equal to that repository's own
    # MAX_BUDGET_USD fallback so a no-DB caller sees the same ceiling.
    max_budget_usd: float = 10.0
    daily_budget_cap_usd: float = 100.0
    monthly_budget_cap_usd: float = 500.0
    budget_overrun_mode: str = "soft"  # "soft" | "hard"

    # Sandbox defaults
    sandbox_image: str = "embry0-sandbox:latest"
    # Memory and CPU limits are per sandbox profile (`sandbox_profiles.memory`
    # / `cpus`); there is no global setting. SANDBOX_MEMORY and SANDBOX_CPUS
    # were declared here and read by nothing (#590), so an operator who still
    # sets them gets config_lint's unknown_env_var warning.
    # #583: most jobs running at once across the fleet; the rest wait in
    # `pending` and start as slots free. 0 = no cap, the behavior before it
    # existed. Size it against DIND_MEMORY (docs/ops/provisioning.md).
    max_running_jobs: int = Field(default=0, ge=0)
    # #307: on SIGTERM the orchestrator stops starting jobs and gives the
    # running ones this long to reach their next node boundary before it
    # stops them; boot then resumes them from their checkpoint. Keep it below
    # the orchestrator's compose stop_grace_period minus uvicorn's own
    # --timeout-graceful-shutdown (docs/ops/deploy.md, "Deploying while jobs run").
    shutdown_drain_seconds: int = Field(default=90, ge=0)
    # #307: how many times boot recovers one job after an unclean stop (crash
    # or kill, no drain) before it fails it with ERR_RESUME_LIMIT instead.
    # A clean drain does not count. 0 = no recovery: every in-flight job
    # fails at boot, as before #307.
    max_resume_attempts: int = Field(default=3, ge=0)

    # Workspace init contexts (RAV-600). `local` contexts stage orchestrator-
    # visible paths into sandboxes — a real exfiltration surface, so the
    # allowlist fails closed: empty = local contexts disabled.
    local_context_allowlist: str = ""  # comma-separated absolute roots
    local_context_max_bytes: int = 209_715_200  # 200 MiB
    local_context_max_files: int = 50_000
    http_context_max_bytes: int = 52_428_800  # 50 MiB
    http_context_timeout_seconds: int = 60
    # http contexts are fetched by the ORCHESTRATOR; private/loopback targets
    # are refused (SSRF) unless a LAN deployment opts in.
    http_context_allow_private: bool = False

    # Container image registry prefix applied to embry0-* images at DinD-launch
    # time (see embry0.execution.image_registry.qualify_image). Empty disables
    # qualification — useful for tests and for environments that load images
    # directly into the daemon. The bootstrapped compose stack runs a sidecar
    # registry on the backend network; on K8s migration this becomes the
    # production registry URL with no code changes.
    image_registry: str = ""
    # Where skill sources are synced to (spec 3.2). The compose file mounts the
    # `skills-store` volume here and at the same path in dind, because the
    # sandbox bind mounts are resolved in dind's namespace.
    skills_store_path: Path = Path("/skills")
    # The path at which the sandbox daemon sees the store; None means the same
    # path as skills_store_path. DinD mounts the volume at /skills too; the
    # rootless daemon runs on the host, so infra/docker-compose.rootless.yml
    # bind-mounts a host directory and sets this to that directory.
    skills_store_daemon_path: Path | None = None
    # The directory a `path` skill source must live under, as the orchestrator
    # sees it; a source that resolves outside it fails its sync. None refuses
    # every `path` source.
    skills_path_root: Path | None = None
    # #523: the in-stack registry refuses anonymous clients. A directory holding
    # `username` and `password` files (compose mounts the registry-auth volume
    # there); at startup the login is written into the orchestrator's docker CLI
    # config for IMAGE_REGISTRY's host. Set but unreadable refuses the start.
    # Empty: no login is written (a registry that needs none, or one whose login
    # an operator supplies through DOCKER_CONFIG).
    registry_auth_dir: str = ""
    # #523: the digests init-push-images recorded when it pushed the embry0-*
    # images, one `<name>:<tag> <digest>` per line. Installed at startup, after
    # which every pinned image runs by digest, so an image re-pushed under the
    # same tag is never used. Set but missing, malformed, or without a pin for
    # the proxy and every builtin sandbox image refuses the start. Empty: images
    # run by tag.
    image_pins_file: str = ""

    # Paused job TTL
    paused_job_ttl_hours: int = 48

    # DinD volume reaper (#274): startup + periodic sweep of volumes whose
    # owning job is terminal or unknown. dry_run logs
    # `volume_reaper_would_remove` and removes nothing. adopt_legacy_names
    # additionally claims unlabelled `embry0-qa-vol-job-<id>` volumes created
    # before ownership labels existed — leave off unless draining that backlog.
    volume_reaper_enabled: bool = True
    volume_reaper_dry_run: bool = False
    volume_reaper_interval_seconds: int = Field(default=3600, ge=60)
    volume_reaper_adopt_legacy_names: bool = False
    volume_reaper_unknown_owner_grace_minutes: int = Field(default=60, ge=0)

    # Off-host backup (#581). The `bucket/prefix` the offhost-backup compose
    # service copies to. Compose hands the orchestrator the same infra/.env
    # value, and the orchestrator only reads whether it is set: set arms
    # embry0_offhost_backup_enabled, the guard of the staleness alert. The
    # status directory is that service's status volume, mounted read-only.
    offhost_backup_s3_path: str = ""
    offhost_backup_status_dir: str = "/run/embry0/offhost-backup"

    @property
    def offhost_backup_enabled(self) -> bool:
        return bool(self.offhost_backup_s3_path.strip())

    # Audit
    audit_log_path: Path | None = None
    # Daily sweeper prunes audit_log rows AND JSONL lines older than this.
    audit_retention_days: int = Field(default=365, gt=0, le=3650)

    # Notifications
    slack_webhook_url: SecretSetting = ""
    telegram_bot_token: SecretSetting = ""
    telegram_chat_id: str = ""
    telegram_webhook_url: str = ""
    # Optional second gate on inbound Telegram replies (#247): comma-separated
    # Telegram user ids allowed to answer. Empty means "anyone in
    # TELEGRAM_CHAT_ID", which is already closed for a private chat but is
    # worth narrowing for a group chat with several members.
    telegram_allowed_sender_ids: str = ""
    # Outbound event webhooks (RAV-1052): signed POSTs for job.completed/
    # job.failed, watcher.proposed, and agent.question. Env fallback for the
    # dashboard-editable integration_config fields of the same name.
    webhook_notify_url: str = ""
    webhook_notify_secret: SecretSetting = ""

    # Public-facing base URL for the dashboard. Used in cross-channel
    # notifications (e.g. GitHub-comment ask-user) to construct deep links the
    # user can click to answer pending questions. Defaults to the local
    # nginx-served dashboard port.
    dashboard_public_url: str = "http://localhost:8200"

    # The public base URL forges deliver webhooks to (a webhooks tunnel
    # hostname, no path). It is the install target of an organization
    # webhook and of the organization hook action (<it>/api/v1/webhook,
    # ADR 0012), and the only origin organization discovery (ADR 0011)
    # counts as "points here": a hook points here only when its URL starts
    # with it. The dashboard and SSO origins above are not webhook origins:
    # the public dashboard answers 404 on webhook paths. Empty or a loopback
    # address (no forge can deliver there): organization webhooks cannot be
    # installed, the hook action is skipped, and a repository's hooks are
    # unknown, not absent.
    webhook_public_url: str = ""

    # Auth0 SSO (RAV-1069). All-or-nothing: sso_enabled requires every core
    # field, INCLUDING the roles claim, and a partial set refuses startup (see
    # _check_sso_config in app.py). The claim is the namespace your tenant's
    # post-login Action stamps onto the ID token; it is deployment-specific and
    # has no default, because a wrong one yields [] for every user — no roles,
    # no access, and nothing in the logs to say why (the documented tier-drift
    # gotcha). A tenant-wide namespace is usually right; an app-specific one is
    # the common mistake. It must be on the ID token, not the access token.
    auth0_issuer: str = ""  # e.g. https://example-tenant.us.auth0.com/ (trailing slash)
    auth0_client_id: str = ""
    auth0_client_secret: SecretSetting = ""
    session_secret: SecretSetting = ""  # signs the embry0_session cookie; >=32 chars
    sso_public_origin: str = ""  # e.g. https://embry0.example.com — must match the registered callback origin
    sso_roles_claim: str = ""  # e.g. https://example.com/roles
    sso_session_max_age_seconds: int = 43200  # 12h absolute; role changes apply at next login

    @property
    def sso_enabled(self) -> bool:
        return bool(
            self.auth0_issuer
            and self.auth0_client_id
            and self.auth0_client_secret
            and self.session_secret
            and self.sso_public_origin
            and self.sso_roles_claim
        )

    # Environment variable encryption. ENVIRONMENT_SECRET_KEY encrypts new
    # secrets; ENVIRONMENT_SECRET_KEYS_OLD is a comma-separated list of
    # previously-active keys (newest first) that decryption still accepts,
    # so the key can be rotated without orphaning stored secrets (RAV-1070).
    environment_secret_key: SecretSetting = ""
    environment_secret_keys_old: SecretSetting = ""

    @property
    def environment_secret_keys_old_list(self) -> list[str]:
        return [k.strip() for k in self.environment_secret_keys_old.split(",") if k.strip()]

    # Shared secret between orchestrator and credential proxies. The orchestrator
    # passes this as PROXY_ADMIN_TOKEN env var into each proxy container; admin
    # endpoints (/admin/enroll, etc.) require it via X-Admin-Token. Required in
    # production. If empty in auth_dev_mode, the orchestrator generates a random
    # value at startup with a warning. Required (refuses to start) otherwise.
    proxy_admin_token: SecretSetting = ""

    # Lifetime of a per-sandbox proxy bearer enrollment (RAV-1070). Backstop for
    # missed unenrolls — must exceed the longest legitimate job. Unenroll on job
    # completion remains the primary revocation path.
    proxy_enroll_ttl_seconds: int = 86_400

    # How long CredentialHealth keeps a rate-limited or quota-exhausted LLM
    # account out of the pool. A past quarantined_until is usable again.
    credential_rate_limit_quarantine_seconds: Annotated[int, NOT_SECRET] = 900
    credential_quota_quarantine_seconds: Annotated[int, NOT_SECRET] = 3600

    # Which daemon runs sandboxes (#268), read once by build_runtime.
    # "dind": the privileged DinD compose service, over TCP+TLS (the default).
    # "rootless": a dedicated rootless Docker daemon on this host, reached by
    # its unix socket (DOCKER_HOST=unix://...); docs/ops/provisioning.md sets
    # it up. It never hands a sandbox the daemon, so profiles with
    # dind_enabled and QA `mode: dind` are refused under it.
    agent_runtime: Literal["dind", "rootless"] = "dind"

    # Docker / DinD
    docker_host: str = ""
    docker_tls_verify: bool = False
    docker_cert_path: str = ""

    # Auth proxy (Anthropic API key injection — currently dead path).
    # Defaults False; only set True when wiring the sandbox to consume
    # auth_proxy_url. The container holds ANTHROPIC_API_KEY in its env and
    # attaches to sandbox-internet, so leaving it on idle is wasted resource +
    # unnecessary attack surface.
    auth_proxy_enabled: bool = False

    # xAI direct-API OAuth path (EMB-45). When enabled, the orchestrator launches
    # the xai-proxy sidecar and owns the rotating SuperGrok refresh token. The
    # durable, Fernet-encrypted credential store is seeded once from the Grok CLI
    # store (``xai_grok_cli_store``, default ~/.grok/auth.json) and then owned by
    # embry0. Requires ENVIRONMENT_SECRET_KEY. Defaults False (opt-in) — leaving it
    # off keeps grok on the EMB-36 CLI/console-key path.
    # Local models (docs/providers.md): the operator's own OpenAI-compatible
    # model server, the custom provider's. Set, the orchestrator launches the
    # local-llm-proxy, which forwards a sandbox's model calls to this one API
    # root (http://host:port/v1) and nowhere else, so no sandbox needs an
    # egress exception for the server's machine. The key is the one the server
    # checks, held by the proxy only; empty sends none. Requests over the size
    # cap are refused. The context window, when set, overrides what the
    # server's /models reports. A run that must use the server waits at most
    # the slot wait for one of its slots (provider_config.local_max_runs).
    local_llm_upstream: Annotated[str, NOT_SECRET] = ""
    local_llm_api_key: SecretSetting = ""
    local_llm_max_request_bytes: Annotated[int, NOT_SECRET] = 8 * 1024 * 1024
    local_llm_context_tokens: Annotated[int, NOT_SECRET] = 0
    local_llm_slot_wait_seconds: Annotated[int, NOT_SECRET] = 900
    # The largest model request the xai- and auth-proxy forward. Every agent
    # turn resends the whole conversation, and a 300k-token prompt is more
    # than a megabyte of JSON. Over it, the run ends as ERR_CONTEXT_WINDOW_EXCEEDED.
    model_proxy_max_request_bytes: Annotated[int, NOT_SECRET] = 32 * 1024 * 1024

    xai_proxy_enabled: bool = False
    xai_credential_path: Annotated[str, NOT_SECRET] = "/data/embry0/xai_credential.enc"
    xai_grok_cli_store: str = ""

    # Deployment-wide default browser backend for the QA agent (#229;
    # docs/glossary.md#browser-backend). Weakest layer of the precedence
    # chain: job override > qa.yaml app_local > app > defaults > THIS.
    # Validated against the backend registry at startup — a typo here must
    # fail loud, not surface as a per-job resolution error.
    qa_browser_backend_default: str = "playwright"

    @field_validator("qa_browser_backend_default")
    @classmethod
    def _qa_browser_backend_registered(cls, v: str) -> str:
        from embry0.workflows.qa.browser_backends import backend_for

        backend_for(v)  # raises ValueError with the known-backends list
        return v

    @field_validator("gitlab_url")
    @classmethod
    def _gitlab_url_is_an_https_host(cls, v: str) -> str:
        """Refuse to start on a GITLAB_URL that is not an https host.

        A plaintext or scheme-less value does not fail loudly: ``api_base()``
        builds a nonsense base, every call 404s or leaks the PAT over http,
        and the clone silently falls back to github.com. Empty means "not
        configured" and resolves to the default (Spec 1A §3.8).
        """
        from urllib.parse import urlparse

        candidate = (v or "").strip().rstrip("/")
        if not candidate:
            return "https://gitlab.com"
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(
                f"GITLAB_URL must be an https:// URL with a hostname (e.g. https://gitlab.example.com); got {v!r}. "
                "Plaintext http would send the PRIVATE-TOKEN in the clear, and a scheme-less value builds a "
                "nonsense API base that silently falls back to github.com."
            )
        return candidate

    # Pluggable agent execution modes (Phase 1).
    # Both dimensions are orthogonal. Defaults preserve today's runtime
    # behavior (SDK path + OAuth from ~/.claude/.credentials.json).
    default_execution_mode: str = "sdk"
    default_auth_mode: str = "oauth"

    # MinIO — QA artifact storage.
    #
    # Two endpoints because the orchestrator and the sandbox see MinIO via
    # different network paths:
    #
    # * ``minio_endpoint`` — internal (orchestrator → minio). Used for bucket
    #   admin (ensure bucket / set lifecycle) and orchestrator-side reads.
    #
    # * ``minio_sandbox_endpoint`` — sandbox-facing. Used to mint presigned
    #   URLs the sandbox will actually hit. The hostname here MUST match the
    #   minio-proxy container name inside DinD (see ProxyManager.start in
    #   Phase 1.5); MinIO presigned-URL signatures cover the request Host
    #   header, so the URL hostname is load-bearing — a mismatch silently
    #   produces SignatureDoesNotMatch on PUT.
    minio_endpoint: str = "minio:9000"
    minio_sandbox_endpoint: str = "minio-proxy:9100"
    minio_root_user: str = ""
    minio_root_password: SecretSetting = ""
    qa_artifact_retention_days: int = Field(default=14, gt=0, le=3650)

    # Code retention (ADR 0017). Unset (the default) keeps everything, as
    # before. Set, a daily sweep replaces the code in job events older than
    # this many days (Write/Edit/Bash inputs, every tool result) with a marker
    # that keeps the tool, file path, size and hash. Ended jobs only.
    job_log_retention_days: int | None = Field(default=None, gt=0, le=3650)
    # Set, a daily sweep deletes the saved agent sessions (the whole
    # conversation) of ended jobs last used more than this many days ago. A
    # pull request comment follow-up on such a job then starts a fresh session
    # instead of resuming, which is why this is not part of the job log setting.
    agent_session_retention_days: int | None = Field(default=None, gt=0, le=3650)
    # Set, a daily sweep removes pre-built QA images (a full checkout each)
    # and per-repo warm volumes unused for this many days, from the sandbox
    # daemon. Never the image an active qa_image_tags row names, nor anything
    # a container uses or a live job's repo needs.
    qa_image_retention_days: int | None = Field(default=None, gt=0, le=3650)

    # Context audit on a schedule (ADR 0020), off until an organization sets
    # settings.context_audit_days. Nobody watches a scheduled audit, so its
    # per-job cap is always a hard stop, as the watcher's is
    # (embry0.services.context_audit); 0, non-finite or invalid falls back to
    # the default, never to unlimited. One tick starts at most this many jobs
    # for each organization, and the due repositories left over wait for the
    # next tick; 0 starts none. Both are the defaults: an organization's
    # settings.context_audit_budget_usd and context_audit_max_jobs_per_tick
    # take their place for it (ADR 0021).
    context_audit_job_budget_usd: float = 2.0
    context_audit_max_jobs_per_tick: int = Field(default=3, ge=0)

    # Leads (spec 2026-10-25): work embry0 finds by itself on the repositories
    # someone answers for (owners set, or a watched stack) and offers as owner
    # proposals. LEADS_ENABLED=false stops the loop; the proposals already
    # stored stay until answered. LEADS_FINDERS names the finders that run:
    # ticket (the backlog intake), ci, alert, flaky, review. Each adds at most
    # LEADS_MAX_NEW_PER_REPO_PER_DAY new leads per repository a day, the worst
    # first. The backlog intake runs every LEADS_BACKLOG_INTERVAL_HOURS, the
    # rest every LEADS_INTERVAL_HOURS.
    leads_enabled: bool = True
    leads_finders: str = "ticket,ci,alert,flaky,review"
    leads_interval_hours: float = Field(default=6.0, gt=0)
    leads_backlog_interval_hours: float = Field(default=24.0, gt=0)
    leads_max_new_per_repo_per_day: int = Field(default=5, ge=0)
    # The lowest readiness (0 to 100) a backlog ticket must score to be offered.
    leads_ticket_min_score: int = Field(default=40, ge=0, le=100)
    # Days a review thread waits for an answer before it is a lead.
    leads_review_stale_days: int = Field(default=3, ge=0)
    # Commits of the default branch the flaky-check finder reads.
    leads_flaky_commits: int = Field(default=10, ge=1, le=50)

    # Owner proposals in plain words (spec 2026-10-25, proposal briefs). A
    # backlog ticket is asked in chat only at this readiness and up; the page
    # shows the rest folded. Each proposal's brief is written on
    # PROPOSAL_BRIEF_MODEL, else on the watcher's local-first model
    # (provider_config.watcher_local_model); with neither, none is written.
    proposals_ticket_min_readiness: int = Field(default=60, ge=0, le=100)
    proposal_brief_model: Annotated[str, NOT_SECRET] = ""
    proposal_brief_interval_seconds: float = Field(default=60.0, gt=0)
    proposal_briefs_per_tick: int = Field(default=3, ge=1, le=20)
    # Jobs in plain words (spec 2026-10-26, job briefs): each job's brief is
    # written on JOB_BRIEF_MODEL, else on the proposal brief model, else on the
    # watcher's local-first model; with none, no job brief is written.
    job_brief_model: Annotated[str, NOT_SECRET] = ""
    job_brief_interval_seconds: float = Field(default=60.0, gt=0)
    job_briefs_per_tick: int = Field(default=3, ge=1, le=20)
    # The Slop-O-Meter (spec 2026-10-26, slop-o-meter): it reads the
    # repositories the world map shows, plus SLOP_METER_REPOS, minus
    # SLOP_METER_EXCLUDE (comma-separated). Each one's bare mirror lives under
    # the data path; the loop runs every interval (0 is off), sooner while a
    # history is still being read, and each tick reads at most these many
    # commits, event pages, pull requests' reviews, logins and outlier
    # judgments per repository. Its forge calls share one budget per tick and
    # stop when the credential's remaining rate limit drops below the reserve
    # (jobs use the same credential). Outliers are judged on the job brief model.
    slop_meter_repos: Annotated[str, NOT_SECRET] = ""
    slop_meter_exclude: Annotated[str, NOT_SECRET] = ""
    slop_meter_api_calls_per_tick: int = Field(default=200, ge=0)
    slop_meter_rate_reserve: int = Field(default=2_000, ge=0)
    slop_meter_interval_seconds: float = Field(default=86_400.0, ge=0)
    slop_meter_catchup_seconds: float = Field(default=300.0, gt=0)
    slop_meter_data_path: Annotated[str, NOT_SECRET] = "/data/embry0/slop-meter"
    slop_meter_git_timeout_seconds: float = Field(default=900.0, gt=0)
    slop_meter_commits_per_tick: int = Field(default=5_000, ge=1)
    slop_meter_event_pages: int = Field(default=10, ge=1)
    slop_meter_reviews_per_tick: int = Field(default=50, ge=0)
    slop_meter_logins_per_tick: int = Field(default=20, ge=0)
    slop_meter_judgments_per_tick: int = Field(default=5, ge=0)

    @field_validator(
        "audit_log_path",
        "job_log_retention_days",
        "agent_session_retention_days",
        "qa_image_retention_days",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @property
    def allowed_cors_origins_list(self) -> list[str]:
        """Parse comma-separated CORS origins."""
        if not self.allowed_cors_origins.strip():
            return ["http://localhost:3001"]
        return [o.strip() for o in self.allowed_cors_origins.split(",") if o.strip()]

    @property
    def trigger_labels_list(self) -> list[str]:
        """Parse comma-separated trigger labels."""
        return [lbl.strip() for lbl in self.trigger_labels.split(",") if lbl.strip()]

    @property
    def telegram_sender_allowlist(self) -> TelegramSenderAllowlist | None:
        """Parse ``TELEGRAM_ALLOWED_SENDER_IDS``; ``None`` means no sender gate.

        When the variable is set, only the integer ids listed are admitted.
        Entries that are not integers (a ``@username``, a stray character)
        are reported in ``invalid`` and never widen the gate: an allowlist
        that parses to nothing admits nobody, because the operator asked for
        a narrower chat, not an open one (#247).
        """
        raw = self.telegram_allowed_sender_ids.strip()
        if not raw:
            return None
        ids: set[int] = set()
        invalid: list[str] = []
        for entry in (part.strip() for part in raw.split(",")):
            if not entry:
                continue
            if re.fullmatch(r"-?\d+", entry):
                ids.add(int(entry))
            else:
                invalid.append(entry)
        return TelegramSenderAllowlist(ids=frozenset(ids), invalid=tuple(invalid))


# Spec 1A §3.8: the prefixes an operator's embry0 configuration actually uses.
# Narrower than config_lint's derived set on purpose — this is the blunt sweep
# ("anything under these four that is not a field"), where config_lint is the
# near-miss detector that stays quiet under prefixes embry0 shares with CI
# runners. Both run at startup; they answer different questions.
_EMBRY0_ENV_PREFIXES: Final[tuple[str, ...]] = ("GITLAB_", "GITHUB_", "LINEAR_", "EMBRY0_")


def unknown_prefixed_env_keys(environ: Mapping[str, str]) -> list[str]:
    """Env keys under an embry0 prefix that no ``Embry0Config`` field declares.

    ``extra="ignore"`` is deliberate — the process env is shared with Docker,
    the shell and the compose stack — but it means ``GITLAB_API_URL`` (the
    field is ``GITLAB_URL``) is dropped in silence and surfaces later as "the
    self-managed host was never contacted". config_lint's near-miss detector
    does not catch that one: ``GITLAB_`` is a prefix embry0 shares with CI
    runners, so it reports only close spellings of a declared name, and this
    is not close enough. Advisory only: the caller logs, nothing fails.
    """
    from embry0.config_lint import _IGNORED_PREFIXES, _UNDECLARED_KEYS, declared_env_names
    from embry0.execution.auth_provider import RESERVED_ENV_KEYS

    declared = declared_env_names(Embry0Config)
    return sorted(
        {
            name
            for name in environ
            if name.startswith(_EMBRY0_ENV_PREFIXES)
            and name not in declared
            and name not in RESERVED_ENV_KEYS
            and name not in _UNDECLARED_KEYS
            and not name.startswith(_IGNORED_PREFIXES)
        }
    )

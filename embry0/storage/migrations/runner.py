"""Simple forward-only migration runner for PostgreSQL.

Concurrency
-----------
``run_migrations`` runs on every orchestrator startup, so a rolling restart, a
second compose stack pointed at a shared Postgres, or a multi-replica K8s
deployment can have two processes migrating one database at the same moment.
Without serialisation both read the same applied set and replay the same
entries: idempotent DDL survives it, but ``INSERT``-style data migrations, an
``ALTER TABLE ... ADD COLUMN`` without ``IF NOT EXISTS``, and even the ledger's
own ``CREATE TABLE IF NOT EXISTS`` (which races at the pg_type level) fail one
replica at startup (#249).

The pass is therefore wrapped in a **session-level** ``pg_advisory_lock``
acquired on the migrating connection before anything else touches the
database, and released in a ``finally``. A session lock rather than
``pg_advisory_xact_lock`` because the latter would force the whole pass into
one outer transaction, and each migration owning its own transaction is
load-bearing: a failing migration rolls back only itself, leaving every
earlier one committed and recorded in the ledger, so the next startup resumes
where it stopped rather than replaying from scratch. The applied set is read
*after* the lock is held, so the replica that waited sees the winner's work
and finds nothing to do. If a connection dies mid-pass, Postgres frees the
session lock when the backend exits.
"""

import asyncio
import time

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

# Key for the Postgres advisory lock that serialises run_migrations() across
# processes. The value is ASCII "embry0" (0x65 6D 62 72 79 30) — arbitrary,
# but FIXED: changing it breaks mutual exclusion against replicas still
# running the old value mid-rollout. Advisory-lock keys share one global,
# database-wide namespace, so any future advisory lock in embry0 MUST pick a
# different key and be registered here (grep `pg_advisory` for the roster —
# this is currently the only one).
MIGRATIONS_ADVISORY_LOCK_KEY = 0x656D62727930

# Acquisition is usually instantaneous; anything slower means another replica
# is mid-migration and this one is queued behind it. Once the wait crosses
# this threshold a log line is emitted *while still blocked*, so the queued
# replica does not look like a stalled startup from the outside.
LOCK_WAIT_LOG_THRESHOLD_SECONDS = 1.0

# Migration 71's backfill rules: (POSIX regex over jobs.error_message, code).
# Pinned literals, not imported from the live classifier: a migration is
# history, and a rule the classifier gains later must not change what
# version 71 did.
# Every pattern is a cluster read off production on 2026-09-20 (#281). The
# three QA patterns are mutually exclusive by construction and mirror
# classify_qa_outcome's precedence (infra > failed > inconclusive).
_M71_BACKFILL_RULES: list[tuple[str, str]] = [
    (
        r"^Separator is (found, but chunk is longer than limit|not found, and chunk exceed the limit)",
        "ERR_AGENT_OUTPUT_OVERFLOW",
    ),
    (r"Reached maximum number of turns", "ERR_AGENT_MAX_TURNS"),
    (r"Command failed with exit code", "ERR_AGENT_PROCESS_FAILED"),
    (r"^Abandoned after [0-9]+ retries$", "ERR_ABANDONED"),
    (r"^triage_malformed:", "ERR_TRIAGE_MALFORMED"),
    (r"Can receive only one value per step", "ERR_STATE_CONCURRENT_UPDATE"),
    (r"^Developer agent did not push branch", "ERR_DEV_BRANCH_NOT_PUSHED"),
    (r"Argument list too long", "ERR_SANDBOX_ARGV_TOO_LONG"),
    (r"^qa_failure_unresolved: triage_qa_failure_action_invalid", "ERR_TRIAGE_QA_ACTION_INVALID"),
    (r"^null value in column .* violates not-null constraint", "ERR_STORAGE"),
    (r"^QA: [0-9]+/[0-9]+ apps passed .*infra: ", "ERR_QA_INFRA"),
    (r"^QA: [0-9]+/[0-9]+ apps passed . failed: [^;]*(; inconclusive: [^;]*)?$", "ERR_QA_FAILED"),
    (r"^QA: [0-9]+/[0-9]+ apps passed . inconclusive: [^;]*$", "ERR_QA_INCONCLUSIVE"),
    (r"^QA final_status=", "ERR_QA_NO_VERDICT"),
    (r"^sandbox profile capability check failed", "ERR_QA_CONFIG_INVALID"),
    (r"^unknown conditional-criteria group", "ERR_QA_CONFIG_INVALID"),
]


def _m71_rules_sql() -> str:
    """The rules as a VALUES body. Literals are embedded (the runner executes
    plain SQL text); both halves are constants above, quotes doubled anyway."""

    def lit(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return ",\n".join(f"            ({lit(pattern)}, {lit(code)})" for pattern, code in _M71_BACKFILL_RULES)


# Each migration is (version, description, sql)
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial schema",
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id          TEXT PRIMARY KEY,
            status          TEXT NOT NULL DEFAULT 'pending',
            repo            TEXT NOT NULL,
            task            TEXT NOT NULL,
            issue_number    INTEGER,
            pipeline_template TEXT,
            pipeline_config JSONB,
            sandbox_profile TEXT,
            total_cost_usd  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            budget_overrun_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            pr_url          TEXT,
            error_message   TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
        CREATE INDEX IF NOT EXISTS idx_jobs_repo ON jobs (repo);
        CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at);

        CREATE TABLE IF NOT EXISTS traces (
            trace_id        TEXT NOT NULL,
            job_id          TEXT NOT NULL REFERENCES jobs(job_id),
            agent_type      TEXT NOT NULL,
            model           TEXT NOT NULL,
            cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            duration_ms     INTEGER NOT NULL DEFAULT 0,
            tools_called    JSONB DEFAULT '{}',
            result          TEXT NOT NULL,
            result_summary  TEXT DEFAULT '',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (trace_id)
        );
        CREATE INDEX IF NOT EXISTS idx_traces_job_id ON traces (job_id);

        CREATE TABLE IF NOT EXISTS pipeline_templates (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            graph_definition JSONB NOT NULL,
            agent_models    JSONB DEFAULT '{}',
            sandbox_profile TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS sandbox_profiles (
            name            TEXT PRIMARY KEY,
            base_image      TEXT NOT NULL DEFAULT 'embry0-sandbox:latest',
            additional_packages JSONB DEFAULT '[]',
            setup_commands  JSONB DEFAULT '[]',
            memory          TEXT NOT NULL DEFAULT '8g',
            cpus            TEXT NOT NULL DEFAULT '4',
            pids_limit      INTEGER NOT NULL DEFAULT 256,
            cap_drop        JSONB DEFAULT '["ALL"]',
            cap_add         JSONB DEFAULT '[]',
            security_opt    JSONB DEFAULT '["no-new-privileges"]',
            agent_timeout_seconds INTEGER NOT NULL DEFAULT 300,
            container_timeout_seconds INTEGER NOT NULL DEFAULT 3600,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS context_config (
            id              TEXT PRIMARY KEY,
            scope           TEXT NOT NULL,
            repo            TEXT,
            system_context  TEXT DEFAULT '',
            assistant_context TEXT DEFAULT '',
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_context_scope ON context_config (scope, repo);

        CREATE TABLE IF NOT EXISTS budget_config (
            id              TEXT PRIMARY KEY DEFAULT 'default',
            max_budget_per_job_usd DOUBLE PRECISION NOT NULL DEFAULT 10.0,
            daily_cap_usd   DOUBLE PRECISION NOT NULL DEFAULT 100.0,
            monthly_cap_usd DOUBLE PRECISION NOT NULL DEFAULT 500.0,
            rate_limit_per_author_per_hour INTEGER NOT NULL DEFAULT 5,
            overrun_mode    TEXT NOT NULL DEFAULT 'soft',
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id              BIGSERIAL PRIMARY KEY,
            action          TEXT NOT NULL,
            actor           TEXT NOT NULL DEFAULT 'system',
            details         JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log (created_at);

        CREATE TABLE IF NOT EXISTS job_logs (
            id              BIGSERIAL PRIMARY KEY,
            job_id          TEXT NOT NULL REFERENCES jobs(job_id),
            agent_id        TEXT,
            stream          TEXT NOT NULL DEFAULT 'stdout',
            text            TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs (job_id);
        """,
    ),
    (
        2,
        "agent definitions, integration config, provider config",
        """
        CREATE TABLE IF NOT EXISTS agent_definitions (
            type            TEXT PRIMARY KEY,
            description     TEXT NOT NULL,
            model           TEXT NOT NULL,
            tools           JSONB DEFAULT '[]',
            skills          JSONB DEFAULT '[]',
            system_prompt   TEXT DEFAULT '',
            is_builtin      BOOLEAN DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS integration_config (
            id                  TEXT PRIMARY KEY,
            trigger_labels      JSONB DEFAULT '["embry0"]',
            webhook_secret      TEXT DEFAULT '',
            slack_webhook_url   TEXT DEFAULT '',
            telegram_bot_token  TEXT DEFAULT '',
            telegram_chat_id    TEXT DEFAULT '',
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS provider_config (
            id              TEXT PRIMARY KEY,
            provider_mode   TEXT DEFAULT 'anthropic_api',
            model_heavy     TEXT DEFAULT 'claude-opus-4-6',
            model_medium    TEXT DEFAULT 'claude-sonnet-4-6',
            model_light     TEXT DEFAULT 'claude-haiku-4-5',
            default_model   TEXT DEFAULT '',
            api_key_set     BOOLEAN DEFAULT false,
            oauth_token_set BOOLEAN DEFAULT false,
            ollama_base_url TEXT DEFAULT '',
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- Seed built-in agents
        INSERT INTO agent_definitions (type, description, model, tools, skills, system_prompt, is_builtin)
        VALUES
            ('triage',
             'Analyzes the issue and configures the optimal pipeline. Assesses complexity, determines confidence, and can request more information or split oversized tasks.',
             'claude-sonnet-4-6', '[]', '[]', '', true),
            ('developer',
             'Implements code changes, manages git operations, and creates pull requests. Uses Claude Code skills for advanced workflows including sub-agent dispatch and worktree management.',
             'claude-opus-4-6', '["Read", "Write", "Edit", "Bash", "Glob", "Grep"]',
             '["superpowers:subagent-driven-development", "superpowers:verification-before-completion"]', '', true),
            ('validator',
             'Validates code changes by running tests, linting, and type checking. Reports pass/fail with detailed findings.',
             'claude-sonnet-4-6', '["Read", "Bash", "Glob", "Grep"]', '[]', '', true),
            ('reviewer',
             'Reviews code changes for correctness, quality, security, and scope. Approves or rejects with feedback.',
             'claude-sonnet-4-6', '["Read", "Glob", "Grep"]', '[]', '', true),
            ('output',
             'Assembles the final job result from all agent outputs. Reports success/failure status, cost, and PR URL.',
             '', '[]', '[]', '', true)
        ON CONFLICT (type) DO NOTHING;

        -- Seed default integration config
        INSERT INTO integration_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING;

        -- Seed default provider config
        INSERT INTO provider_config (id) VALUES ('default') ON CONFLICT (id) DO NOTHING;
        """,
    ),
    (
        3,
        "add description column to pipeline_templates",
        """
        ALTER TABLE pipeline_templates ADD COLUMN IF NOT EXISTS description TEXT DEFAULT '';
        """,
    ),
    (
        4,
        "Add issues table, issues FK on jobs, issue_id on audit_log",
        """
        CREATE TABLE IF NOT EXISTS issues (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            body            TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'open',
            priority        TEXT NOT NULL DEFAULT 'medium',
            labels          JSONB NOT NULL DEFAULT '[]',
            repo            TEXT,
            parent_issue_id TEXT REFERENCES issues(id),
            github_number   INTEGER,
            github_url      TEXT,
            github_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            github_synced_at TIMESTAMPTZ,
            created_by      TEXT NOT NULL DEFAULT 'user',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_issues_status ON issues (status);
        CREATE INDEX IF NOT EXISTS idx_issues_priority ON issues (priority);
        CREATE INDEX IF NOT EXISTS idx_issues_repo ON issues (repo);
        CREATE INDEX IF NOT EXISTS idx_issues_parent ON issues (parent_issue_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_github
            ON issues (repo, github_number) WHERE github_number IS NOT NULL;

        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS issue_id TEXT REFERENCES issues(id);
        CREATE INDEX IF NOT EXISTS idx_jobs_issue_id ON jobs (issue_id);

        ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS issue_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_audit_issue_id ON audit_log (issue_id);
        """,
    ),
    (
        5,
        "Add issue_inputs table for human-in-the-loop questions",
        """
        CREATE TABLE IF NOT EXISTS issue_inputs (
            id              TEXT PRIMARY KEY,
            issue_id        TEXT NOT NULL REFERENCES issues(id),
            job_id          TEXT NOT NULL REFERENCES jobs(job_id),
            asking_node     TEXT NOT NULL DEFAULT 'triage',
            question        TEXT NOT NULL,
            importance      TEXT NOT NULL DEFAULT 'blocking',
            auto_answer     TEXT,
            answer          TEXT,
            answered_by     TEXT,
            telegram_message_id INTEGER,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            answered_at     TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_issue_inputs_issue_id ON issue_inputs (issue_id);
        CREATE INDEX IF NOT EXISTS idx_issue_inputs_job_id ON issue_inputs (job_id);
        CREATE INDEX IF NOT EXISTS idx_issue_inputs_status ON issue_inputs (status);
        CREATE INDEX IF NOT EXISTS idx_issue_inputs_telegram_msg ON issue_inputs (telegram_message_id)
            WHERE telegram_message_id IS NOT NULL;
        """,
    ),
    (
        6,
        "remove validator/output agents, rename reviewer to review",
        """
        DELETE FROM agent_definitions WHERE type IN ('validator', 'output');
        UPDATE agent_definitions SET type = 'review',
            description = 'Reviews code changes by running tests, linting, type checking, and code review. Returns structured JSON with decision, validation results, and documentation review.',
            tools = '["Read", "Bash", "Glob", "Grep"]'
        WHERE type = 'reviewer';
        """,
    ),
    (
        7,
        "add updated_at to jobs table with auto-update trigger",
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

        -- Backfill existing rows: use created_at as initial updated_at
        UPDATE jobs SET updated_at = COALESCE(started_at, created_at) WHERE updated_at IS NULL;

        -- Create trigger function for auto-updating updated_at
        CREATE OR REPLACE FUNCTION update_jobs_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        -- Attach trigger to jobs table
        DROP TRIGGER IF EXISTS trg_jobs_updated_at ON jobs;
        CREATE TRIGGER trg_jobs_updated_at
            BEFORE UPDATE ON jobs
            FOR EACH ROW
            EXECUTE FUNCTION update_jobs_updated_at();

        CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs (updated_at);
        """,
    ),
    (
        8,
        "add error_code column to jobs",
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS error_code TEXT;
        CREATE INDEX IF NOT EXISTS idx_jobs_error_code ON jobs (error_code) WHERE error_code IS NOT NULL;
        """,
    ),
    (
        9,
        "add trace_id to jobs and audit_log for cross-span correlation",
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS trace_id TEXT;
        ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS trace_id TEXT;
        CREATE INDEX IF NOT EXISTS idx_jobs_trace_id ON jobs (trace_id) WHERE trace_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_audit_trace_id ON audit_log (trace_id) WHERE trace_id IS NOT NULL;
        """,
    ),
    (
        10,
        "environment tables — global + per-repo env vars with secret masking",
        """
        CREATE TABLE IF NOT EXISTS global_environment (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            var_type    TEXT NOT NULL DEFAULT 'config' CHECK (var_type IN ('config', 'secret')),
            description TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS repo_environment (
            repo        TEXT NOT NULL,
            key         TEXT NOT NULL,
            value       TEXT NOT NULL,
            var_type    TEXT NOT NULL DEFAULT 'config' CHECK (var_type IN ('config', 'secret')),
            description TEXT DEFAULT '',
            required    BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (repo, key)
        );
        CREATE INDEX IF NOT EXISTS idx_repo_environment_repo ON repo_environment (repo);
        """,
    ),
    (
        11,
        "repo_preferences — per-repo sandbox profile + language hints",
        """
        CREATE TABLE IF NOT EXISTS repo_preferences (
            repo             TEXT PRIMARY KEY,
            sandbox_profile  TEXT,
            language_hint    TEXT,
            notes            TEXT DEFAULT '',
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
    ),
    (
        12,
        "pluggable agent execution modes — execution_mode + auth_mode columns",
        """
        ALTER TABLE repo_preferences
            ADD COLUMN IF NOT EXISTS execution_mode TEXT,
            ADD COLUMN IF NOT EXISTS auth_mode TEXT;

        ALTER TABLE agent_definitions
            ADD COLUMN IF NOT EXISTS execution_mode TEXT,
            ADD COLUMN IF NOT EXISTS auth_mode TEXT;
        """,
    ),
    (
        13,
        "drop untouched provider/integration config seed rows so env defaults flow through",
        # Migration #2 seeded provider_config and integration_config with id='default'
        # at the original hardcoded defaults. Those seed rows pin DB state to the
        # hardcoded values and prevent the .env values from being reflected in the
        # Settings UI. Delete the rows IFF every column matches the original seed —
        # that signals the user has not customized anything via the UI.
        # Repos fall back to env-derived defaults whenever the row is absent.
        """
        DELETE FROM provider_config
        WHERE id = 'default'
          AND provider_mode = 'anthropic_api'
          AND model_heavy = 'claude-opus-4-6'
          AND model_medium = 'claude-sonnet-4-6'
          AND model_light = 'claude-haiku-4-5'
          AND COALESCE(default_model, '') = ''
          AND COALESCE(ollama_base_url, '') = ''
          AND api_key_set = false
          AND oauth_token_set = false;

        DELETE FROM integration_config
        WHERE id = 'default'
          AND trigger_labels = '["embry0"]'::jsonb
          AND COALESCE(webhook_secret, '') = ''
          AND COALESCE(slack_webhook_url, '') = ''
          AND COALESCE(telegram_bot_token, '') = ''
          AND COALESCE(telegram_chat_id, '') = '';
        """,
    ),
    (
        14,
        "bump default developer agent model to claude-opus-4-7",
        # Migration #2 seeded the developer agent_definitions row with
        # claude-opus-4-6. Now that 4-7 is GA we want new installs (and any
        # existing install that hasn't customized this row) to use it.
        # Only update IFF the model is still at the original seed value, so
        # users who explicitly chose a different model are not clobbered.
        """
        UPDATE agent_definitions
        SET model = 'claude-opus-4-7'
        WHERE type = 'developer'
          AND model = 'claude-opus-4-6'
          AND is_builtin = true;
        """,
    ),
    (
        15,
        "FK ON DELETE policies — CASCADE for child rows, SET NULL for soft references",
        # Redefine every FK with an explicit ON DELETE clause.
        # Policy:
        #   CASCADE  — child rows are owned by their parent; delete together.
        #   SET NULL — child rows can survive parent deletion (soft reference).
        #
        # Not FKs (soft references, intentionally):
        #   audit_log.issue_id — audit must outlive any deleted entity; no FK by design.
        #   audit_log.trace_id — same audit-trail rationale.
        """
        -- traces.job_id: CASCADE (traces are owned by their job)
        ALTER TABLE traces DROP CONSTRAINT IF EXISTS traces_job_id_fkey;
        ALTER TABLE traces ADD CONSTRAINT traces_job_id_fkey
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE;

        -- job_logs.job_id: CASCADE (logs are owned by their job)
        ALTER TABLE job_logs DROP CONSTRAINT IF EXISTS job_logs_job_id_fkey;
        ALTER TABLE job_logs ADD CONSTRAINT job_logs_job_id_fkey
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE;

        -- issue_inputs.issue_id: CASCADE (inputs are owned by their issue)
        ALTER TABLE issue_inputs DROP CONSTRAINT IF EXISTS issue_inputs_issue_id_fkey;
        ALTER TABLE issue_inputs ADD CONSTRAINT issue_inputs_issue_id_fkey
            FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE;

        -- issue_inputs.job_id: CASCADE (inputs are owned by their job)
        ALTER TABLE issue_inputs DROP CONSTRAINT IF EXISTS issue_inputs_job_id_fkey;
        ALTER TABLE issue_inputs ADD CONSTRAINT issue_inputs_job_id_fkey
            FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE;

        -- jobs.issue_id: SET NULL (jobs survive issue deletion)
        ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_issue_id_fkey;
        ALTER TABLE jobs ADD CONSTRAINT jobs_issue_id_fkey
            FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE SET NULL;

        -- issues.parent_issue_id: SET NULL (children survive parent deletion)
        ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_parent_issue_id_fkey;
        ALTER TABLE issues ADD CONSTRAINT issues_parent_issue_id_fkey
            FOREIGN KEY (parent_issue_id) REFERENCES issues(id) ON DELETE SET NULL;

        -- audit_log.issue_id: soft reference — no FK by design (audit outlives entities)
        -- audit_log.trace_id: soft reference — no FK by design (audit outlives entities)
        """,
    ),
    (
        16,
        "performance indexes — GIN on labels/title/body, composite on job_logs+traces",
        # Note: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
        # The migration runner wraps each migration in conn.transaction(), so
        # CONCURRENTLY is intentionally omitted. Plain CREATE INDEX takes a
        # ShareLock briefly but is correct on embry0's dataset sizes.
        """
        CREATE EXTENSION IF NOT EXISTS pg_trgm;

        -- GIN index for labels JSONB containment queries (issues.labels @> $1::jsonb)
        CREATE INDEX IF NOT EXISTS idx_issues_labels_gin
            ON issues USING GIN (labels);

        -- Trigram GIN indexes for ILIKE full-text search on title and body
        CREATE INDEX IF NOT EXISTS idx_issues_title_trgm
            ON issues USING GIN (title gin_trgm_ops);
        CREATE INDEX IF NOT EXISTS idx_issues_body_trgm
            ON issues USING GIN (body gin_trgm_ops);

        -- Composite index for log replay cursor query:
        --   WHERE job_id=$1 AND id > $2 ORDER BY id ASC LIMIT $3
        CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id
            ON job_logs (job_id, id);

        -- Composite indexes for trace filtering by agent_type and result
        CREATE INDEX IF NOT EXISTS idx_traces_job_agent
            ON traces (job_id, agent_type);
        CREATE INDEX IF NOT EXISTS idx_traces_job_result
            ON traces (job_id, result);
        """,
    ),
    (
        17,
        "unify trace_id prefix from trace- to trc- in traces table",
        # traces.trace_id rows created before this migration use the 'trace-' prefix.
        # jobs.trace_id and audit_log.trace_id have always used 'trc-'.
        # Standardise on 'trc-' so all IDs in the system follow the same convention.
        # Idempotent: rows already starting with 'trc-' do not match the LIKE clause.
        """
        UPDATE traces
           SET trace_id = REPLACE(trace_id, 'trace-', 'trc-')
         WHERE trace_id LIKE 'trace-%';
        """,
    ),
    (
        18,
        "sandbox_profiles — QA agent foundation columns",
        # description: human-readable text shown in /sandboxes UI
        # dind_enabled: when true, sandbox gets DOCKER_HOST + DinD certs mounted
        # idle_timeout_seconds: kill the sandbox if no agent activity for this long (default 600)
        # extra_networks: extra docker networks to join in addition to sandbox-restricted (jsonb array)
        # env_defaults: profile-level env-var defaults (non-secret, jsonb object)
        # is_builtin: marks profiles seeded by embry0; UI hides destructive ops on these
        """
        ALTER TABLE sandbox_profiles
            ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS dind_enabled BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS idle_timeout_seconds INTEGER NOT NULL DEFAULT 600,
            ADD COLUMN IF NOT EXISTS extra_networks JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS env_defaults JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN NOT NULL DEFAULT false;
        """,
    ),
    (
        19,
        "env tables — scope column for app/qa segregation",
        # scope='app' is injected into every sandbox; scope='qa' only when pipeline=qa
        # or the issue→PR job's qa-state has needs_qa=true. Enforced at sandbox
        # injection time in workflows/issue_to_pr/nodes.py.
        """
        ALTER TABLE global_environment
            ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'app'
                CHECK (scope IN ('app', 'qa'));
        ALTER TABLE repo_environment
            ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'app'
                CHECK (scope IN ('app', 'qa'));
        CREATE INDEX IF NOT EXISTS idx_global_env_scope ON global_environment (scope);
        CREATE INDEX IF NOT EXISTS idx_repo_env_scope ON repo_environment (repo, scope);
        """,
    ),
    (
        20,
        "agent_definitions — mcp_servers JSONB column for MCP-aware agents",
        # The QA agent needs to launch the Playwright MCP server. BUILTIN_SEED
        # already declares mcp_servers; this migration makes the DB schema
        # accept it. Default '{}' so legacy rows are unaffected.
        """
        ALTER TABLE agent_definitions
            ADD COLUMN IF NOT EXISTS mcp_servers JSONB NOT NULL DEFAULT '{}'::jsonb;
        """,
    ),
    (
        21,
        "issues — notification_channels JSONB column (which channels get agent questions)",
        # Per-issue list of channels (dashboard / telegram / github) to dispatch
        # agent ask-user questions to. Default ["dashboard"] preserves existing
        # behavior — every legacy row keeps the dashboard form as its sole
        # channel. Subsequent code (Tasks 4/5) reads this as a list of strings.
        """
        ALTER TABLE issues
            ADD COLUMN IF NOT EXISTS notification_channels JSONB
                NOT NULL DEFAULT '["dashboard"]'::jsonb;
        """,
    ),
    (
        22,
        "issue_inputs — add skipped_by/skipped_at audit columns for /skip directive",
        # The inbound dispatcher (Plan B Task 2) introduces a `skip(input_id,
        # skipped_by)` repository method that mirrors `answer()`. The status
        # column is plain TEXT (no CHECK), so 'skipped' is already a legal
        # value — but we need audit columns to record who skipped and when.
        """
        ALTER TABLE issue_inputs
            ADD COLUMN IF NOT EXISTS skipped_by TEXT;
        ALTER TABLE issue_inputs
            ADD COLUMN IF NOT EXISTS skipped_at TIMESTAMPTZ;
        """,
    ),
    (
        23,
        "agent_sessions — per-(job, agent) conversation state for resume continuity",
        # Plan C Task 1. Stores SDK conversation state so a paused agent
        # (ask-user interrupt) can resume from exactly where it left off
        # instead of being re-launched fresh with prior turns stitched into
        # a prompt addendum. Two persistence formats live side-by-side:
        #   anthropic_api mode → messages JSONB (full message history)
        #   claude_max mode    → session_id + session_blob (CLI session file)
        # ON DELETE CASCADE so sessions die with their parent job.
        # UNIQUE (job_id, agent_type) — one session per (job, agent) pair.
        # Index on job_id supports the upcoming delete_for_job(job_id) path.
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id           text PRIMARY KEY,
            job_id       text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            agent_type   text NOT NULL,
            mode         text NOT NULL,
            session_id   text NULL,
            messages     jsonb NULL,
            session_blob bytea NULL,
            last_turn_at timestamptz NOT NULL DEFAULT now(),
            created_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT agent_sessions_job_id_agent_type_key UNIQUE (job_id, agent_type)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_job ON agent_sessions(job_id);
        """,
    ),
    (
        24,
        "qa_app_results — per-app QA outcomes for multi-app monorepo runs",
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;

        CREATE TABLE IF NOT EXISTS qa_app_results (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_id          TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            app_name        TEXT NOT NULL,
            status          TEXT NOT NULL CHECK (status IN (
                'passed','qa_failure','e2e_failure','boot_failure',
                'ready_check_failed','infra_failure','skipped'
            )),
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_ms     INTEGER NOT NULL DEFAULT 0,
            cache_hits      JSONB NOT NULL DEFAULT '{}'::jsonb,
            trace_url       TEXT,
            failure_summary TEXT,
            raw_result      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_app_results_job_app
            ON qa_app_results (job_id, app_name);

        CREATE INDEX IF NOT EXISTS ix_qa_app_results_job
            ON qa_app_results (job_id);

        CREATE INDEX IF NOT EXISTS ix_qa_app_results_status
            ON qa_app_results (status);
        """,
    ),
    (
        25,
        "qa_app_results — extend status CHECK to include 'inconclusive'",
        """
        ALTER TABLE qa_app_results
            DROP CONSTRAINT IF EXISTS qa_app_results_status_check;
        ALTER TABLE qa_app_results
            ADD CONSTRAINT qa_app_results_status_check CHECK (status IN (
                'passed','qa_failure','e2e_failure','boot_failure',
                'ready_check_failed','infra_failure','inconclusive','skipped'
            ));
        """,
    ),
    (
        26,
        "qa_image_tags — pre-baked sandbox image registry per repo",
        """
        CREATE TABLE IF NOT EXISTS qa_image_tags (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            repo            TEXT NOT NULL,
            image_tag       TEXT NOT NULL,
            lockfile_sha    TEXT NOT NULL,
            built_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            built_by        TEXT NOT NULL DEFAULT 'manual',
            is_active       BOOLEAN NOT NULL DEFAULT true,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
        );

        CREATE INDEX IF NOT EXISTS ix_qa_image_tags_repo_active
            ON qa_image_tags (repo, is_active, built_at DESC);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_image_tags_repo_lockfile
            ON qa_image_tags (repo, lockfile_sha);
        """,
    ),
    (
        27,
        "qa_volume_state — shared-volume warmth tracking per (repo, scope)",
        """
        CREATE TABLE IF NOT EXISTS qa_volume_state (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope               TEXT NOT NULL CHECK (scope IN ('per-job','per-repo','per-org')),
            scope_key           TEXT NOT NULL,
            volume_name         TEXT NOT NULL,
            last_warmed_sha     TEXT,
            last_warmed_at      TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_volume_state_scope
            ON qa_volume_state (scope, scope_key);
        """,
    ),
    (
        28,
        "qa_app_results — boot_phase JSONB for per-sub-task boot drilldown",
        # Phase 5A: surface ready_check failed_checks + dev-server stdout tail
        # in the dashboard. Populated by collect_artifacts_node when the boot
        # phase did not pass; NULL on legacy rows and on the passed path.
        """
        ALTER TABLE qa_app_results
            ADD COLUMN IF NOT EXISTS boot_phase JSONB NULL;
        COMMENT ON COLUMN qa_app_results.boot_phase IS
            '{outcome, attempts, duration_ms, failed_checks: [str], boot_stdout_tail: str} populated by collect_artifacts_node when boot_outcome != "passed". NULL otherwise.';
        """,
    ),
    (
        29,
        "qa_run_metadata — affected-set + diff snapshot per QA run",
        # Phase 5D: persist the run-level affected-set decision so the dashboard
        # can show which apps ran vs were skipped, what files drove the decision,
        # and (eventually) the dep graph that produced it. One row per QA run,
        # written by qa_orchestrator_node after fan-out resolves.
        """
        CREATE TABLE IF NOT EXISTS qa_run_metadata (
            job_id          TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            apps_to_qa      TEXT[] NOT NULL,
            apps_skipped    TEXT[] NOT NULL,
            force_all_apps  BOOLEAN NOT NULL,
            changed_files   TEXT[] NOT NULL DEFAULT '{}',
            base_branch     TEXT NOT NULL DEFAULT '',
            dep_graph       JSONB NOT NULL DEFAULT '[]',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE qa_run_metadata IS
            'Run-level affected-set + diff metadata captured by qa_orchestrator_node. One row per QA run.';
        """,
    ),
    (
        30,
        "recast legacy JSON-string-scalar JSONB rows to real JSONB objects",
        # Pre-fix bug: the asyncpg JSONB codec calls json.dumps() once on every
        # parameter; several QA repos ALSO called json.dumps() before passing
        # the value to a $N::jsonb parameter. Postgres parsed the resulting
        # double-encoded string and stored it as a JSONB string scalar
        # (jsonb_typeof = 'string') — so server-side ->> / -> operators saw a
        # string, not the inner object, and silently returned NULL.
        #
        # The codec + repo upserts have been fixed (codec encodes once; repos
        # pass Python objects). This migration recasts any rows still in the
        # broken shape to the correct shape via ``(col #>> '{}')::jsonb``,
        # which extracts the JSON value as text and reparses as JSONB.
        #
        # Idempotent: ``WHERE jsonb_typeof(col) = 'string'`` makes re-runs a
        # no-op once the data has been migrated.
        """
        UPDATE qa_app_results
           SET cache_hits = (cache_hits #>> '{}')::jsonb
         WHERE jsonb_typeof(cache_hits) = 'string';

        UPDATE qa_app_results
           SET raw_result = (raw_result #>> '{}')::jsonb
         WHERE jsonb_typeof(raw_result) = 'string';

        UPDATE qa_app_results
           SET boot_phase = (boot_phase #>> '{}')::jsonb
         WHERE boot_phase IS NOT NULL
           AND jsonb_typeof(boot_phase) = 'string';

        UPDATE qa_image_tags
           SET metadata = (metadata #>> '{}')::jsonb
         WHERE jsonb_typeof(metadata) = 'string';

        UPDATE qa_run_metadata
           SET dep_graph = (dep_graph #>> '{}')::jsonb
         WHERE jsonb_typeof(dep_graph) = 'string';
        """,
    ),
    (
        31,
        "qa_workspace_provider_overrides — per-repo dashboard-set provider config",
        # Phase 5G: operators can override .embry0/qa.yaml's workspace_provider
        # section per-repo from the dashboard admin UI. When a row exists for a
        # repo, the orchestrator prefers it over the file-based config; when it
        # doesn't, the file-based config flows through unchanged.
        """
        CREATE TABLE IF NOT EXISTS qa_workspace_provider_overrides (
            repo            TEXT PRIMARY KEY,
            provider_type   TEXT NOT NULL,
            config          JSONB NOT NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        COMMENT ON TABLE qa_workspace_provider_overrides IS
            'Per-repo workspace_provider overrides set via the dashboard admin UI. When a row exists, the orchestrator prefers it over the .embry0/qa.yaml workspace_provider section.';
        """,
    ),
    (
        32,
        "qa_run_metadata — head_sha column for flake-heatmap aggregation",
        # Phase 5F: persist the orchestrator's resolved head_sha so the
        # flake-heatmap aggregator can detect status changes between
        # consecutive runs on the SAME workspace head. Empty string default
        # keeps legacy rows valid (they predate the column); the
        # flake_window query filters them out via head_sha != ''.
        """
        ALTER TABLE qa_run_metadata
            ADD COLUMN IF NOT EXISTS head_sha TEXT NOT NULL DEFAULT '';
        COMMENT ON COLUMN qa_run_metadata.head_sha IS
            'Git SHA of the workspace head when the QA run started. Empty string for legacy rows that predate this column. Used by flake-heatmap aggregation in Phase 5F.';
        """,
    ),
    (
        33,
        "pipeline_templates — is_builtin flag for canonical seed pipelines",
        # Mirrors the sandbox_profiles pattern: builtin templates are upserted
        # at orchestrator startup and identified by is_builtin=true so the UI
        # can badge them and the API can refuse non-seed deletes/edits to them.
        #
        # Numbered last (33) intentionally: this migration came from `main`
        # and collided with the branch's v24 (qa_app_results). Appending it
        # as the highest version keeps every branch migration at its
        # original number, so the version-pinned migration tests (e.g.
        # test_migration_30 = the legacy-JSONB recast) stay valid.
        """
        ALTER TABLE pipeline_templates
            ADD COLUMN IF NOT EXISTS is_builtin BOOLEAN NOT NULL DEFAULT FALSE;
        """,
    ),
    (
        34,
        "jobs — make repo nullable + add context JSONB for non-code jobs",
        # Repo becomes optional; a job's context is a typed object
        # (git|http|local|none). Existing rows are all git, so backfill their
        # context from repo. New non-git jobs write context directly and leave
        # repo NULL.
        """
        ALTER TABLE jobs ALTER COLUMN repo DROP NOT NULL;
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS context JSONB;
        UPDATE jobs
            SET context = jsonb_build_object('type', 'git', 'repo', repo)
            WHERE context IS NULL AND repo IS NOT NULL;
        COMMENT ON COLUMN jobs.context IS
            'Typed job context (git|http|local|none). NULL only for pre-migration rows with a NULL repo, which never existed before this migration.';
        """,
    ),
    (
        35,
        "jobs — current_stage column for live pipeline-stage surfacing",
        # Live Console board (2026-07-08 design): the executor persists the
        # workflow's current_stage on the jobs row at each node transition so
        # the board can render a stage badge from the poll alone — no WS or
        # log fetch per job, and the board stays correct when a WS drops.
        # Nullable by design: legacy rows (and jobs that never streamed a
        # stage) stay NULL and the API tolerates that.
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS current_stage TEXT;
        COMMENT ON COLUMN jobs.current_stage IS
            'Latest workflow current_stage observed by the executor stream (e.g. triage_complete, review_passed). NULL for rows that predate this column.';
        """,
    ),
    (
        36,
        "sandbox_profiles — extra_hosts for deployed-target QA (EMB-28)",
        # extra_hosts: hostname -> IP map emitted as --add-host flags at sandbox
        # create. Lets a browser-capable sandbox on sandbox-internet reach an
        # externally deployed app by its real vhost name (DinD NAT is by-IP
        # only; Docker DNS cannot resolve host/LAN names from nested
        # sandboxes). The 'dind' alias stays orchestrator-owned and cannot be
        # overridden from a profile.
        """
        ALTER TABLE sandbox_profiles
            ADD COLUMN IF NOT EXISTS extra_hosts JSONB NOT NULL DEFAULT '{}'::jsonb;
        """,
    ),
    (
        37,
        "qa_run_metadata — conditional_groups for relevance-gated criteria (EMB-39)",
        # Which conditional acceptance-criteria groups fired on this run and
        # why, so the dashboard's affected-set view can show the relevance
        # decision alongside the diff that drove it. Default [] keeps legacy
        # rows valid.
        """
        ALTER TABLE qa_run_metadata
            ADD COLUMN IF NOT EXISTS conditional_groups JSONB NOT NULL DEFAULT '[]'::jsonb;
        COMMENT ON COLUMN qa_run_metadata.conditional_groups IS
            'EMB-39: [{"name": str, "source": "matched"|"forced", "apps": [str]}] conditional acceptance-criteria groups applied to the run. [] for legacy rows and runs with no fired groups.';
        """,
    ),
    (
        38,
        "traces — per-run token usage columns (EMB-35)",
        # Token-level observability: the CLI's ResultMessage.usage exposes
        # input/output plus cache read/creation token counts, which the
        # executor previously dropped after emitting a transient cost_update
        # stream event. Persisting them per trace makes per-phase token cost
        # and cache-hit rate measurable (the EMB-35 success metric). DEFAULT 0
        # keeps legacy rows valid; 0 on a legacy row means "not recorded",
        # not "free".
        """
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS input_tokens BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS output_tokens BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS cache_read_tokens BIGINT NOT NULL DEFAULT 0;
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS cache_creation_tokens BIGINT NOT NULL DEFAULT 0;
        COMMENT ON COLUMN traces.input_tokens IS
            'EMB-35: non-cached input tokens from ResultMessage.usage.input_tokens. 0 for rows that predate this column.';
        COMMENT ON COLUMN traces.output_tokens IS
            'EMB-35: output tokens from ResultMessage.usage.output_tokens. 0 for rows that predate this column.';
        COMMENT ON COLUMN traces.cache_read_tokens IS
            'EMB-35: prompt-cache read tokens (usage.cache_read_input_tokens). 0 for rows that predate this column.';
        COMMENT ON COLUMN traces.cache_creation_tokens IS
            'EMB-35: prompt-cache creation tokens (usage.cache_creation_input_tokens). 0 for rows that predate this column.';
        """,
    ),
    (
        39,
        "issue_inputs — nullable issue_id for issue-less jobs (EMB-43)",
        # Direct POST /jobs runs dispatch with issue_id=None by design, but
        # any agent question crashed the whole workflow on this NOT NULL.
        # Rows keep job_id (already indexed); NULL issue_id = issue-less job.
        """
        ALTER TABLE issue_inputs ALTER COLUMN issue_id DROP NOT NULL;
        """,
    ),
    (
        40,
        "issues — linear_identifier + linear_issue_id for the Linear trigger (EMB-47)",
        # Linear-webhook-dispatched issues carry their source ticket so
        # redelivered/label-churn webhooks dedupe (partial unique index) and
        # the write-back channel knows which Linear issue to comment on.
        # linear_issue_id is Linear's UUID (needed by the commentCreate
        # mutation); linear_identifier is the human key (EMB-48).
        """
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS linear_identifier TEXT;
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS linear_issue_id TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_linear_identifier
            ON issues (linear_identifier) WHERE linear_identifier IS NOT NULL;
        """,
    ),
    (
        41,
        "repo_preferences — per-repo git author identity override (EMB-51)",
        # NULL = fall back to the branding default (env-overridable). Set when
        # a target org's push rules reject the default identity's email.
        """
        ALTER TABLE repo_preferences
            ADD COLUMN IF NOT EXISTS git_author_name TEXT,
            ADD COLUMN IF NOT EXISTS git_author_email TEXT;
        """,
    ),
    (
        42,
        "jobs.job_kind + pipeline_templates.default_for_kind (RAV-601)",
        # job_kind: triage's classification (code/research/analysis/ops),
        # mirrored from state for dashboard filtering. default_for_kind: at
        # most one template per kind serves as that kind's default route
        # (partial unique index enforces the at-most-one).
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_kind TEXT;
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS result_summary TEXT;
        ALTER TABLE pipeline_templates ADD COLUMN IF NOT EXISTS default_for_kind TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_templates_default_kind
            ON pipeline_templates (default_for_kind) WHERE default_for_kind IS NOT NULL;
        """,
    ),
    (
        43,
        "deliverables — typed job outputs beyond the PR (RAV-603)",
        # One row per deliverable a job produced. type vocabulary:
        #   pr       → url points at the pull request (code kinds)
        #   report   → content holds the markdown deliverable (research/
        #              analysis/ops via finalize_output)
        #   artifact → storage_bucket/storage_key locate the file in MinIO
        #              (files the agent left in /workspace/deliverables/)
        #   message  → content holds a short free-text answer
        # jobs.result_summary stays as a denormalized convenience for list
        # views / completion comments; this table is the real model.
        # ON DELETE CASCADE so deliverables die with their parent job.
        """
        CREATE TABLE IF NOT EXISTS deliverables (
            id             TEXT PRIMARY KEY,
            job_id         TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            type           TEXT NOT NULL CHECK (type IN ('pr', 'report', 'artifact', 'message')),
            title          TEXT NOT NULL DEFAULT '',
            content        TEXT,
            url            TEXT,
            storage_bucket TEXT,
            storage_key    TEXT,
            media_type     TEXT,
            size_bytes     BIGINT,
            metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_deliverables_job ON deliverables (job_id);
        """,
    ),
    (
        44,
        "watcher_runs — audit trail for the log-watcher/proposer (RAV-657)",
        # One row per watcher tick. action vocabulary:
        #   skipped_quiet     → matching log volume below the minimum
        #   skipped_backlog   → open-proposal cap reached, tick not run
        #   skipped_duplicate → analysis produced a proposal we already filed
        #   no_issue          → analysis ran, agent found nothing ticket-worthy
        #   proposed          → draft Linear ticket filed + human pinged
        #   error             → the tick itself failed (detail column)
        # fingerprint dedups proposals across ticks (normalized title hash);
        # linear_issue_identifier records the filed draft (e.g. RAV-1099).
        """
        CREATE TABLE IF NOT EXISTS watcher_runs (
            id                       TEXT PRIMARY KEY,
            window_start             TIMESTAMPTZ NOT NULL,
            window_end               TIMESTAMPTZ NOT NULL,
            log_lines                INTEGER NOT NULL DEFAULT 0,
            action                   TEXT NOT NULL,
            detail                   TEXT,
            job_id                   TEXT,
            fingerprint              TEXT,
            linear_issue_identifier  TEXT,
            created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_watcher_runs_created ON watcher_runs (created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_watcher_runs_fingerprint ON watcher_runs (fingerprint)
            WHERE fingerprint IS NOT NULL;
        """,
    ),
    (
        45,
        "integration_config — outbound notification webhook url + secret (RAV-1052)",
        # Target for the WebhookNotifier's signed event POSTs (job.completed/
        # job.failed, watcher.proposed, agent.question). Dashboard-editable
        # via PUT /config/integrations; WEBHOOK_NOTIFY_* env vars act as the
        # fallback when the row fields are empty.
        """
        ALTER TABLE integration_config ADD COLUMN IF NOT EXISTS webhook_notify_url TEXT DEFAULT '';
        ALTER TABLE integration_config ADD COLUMN IF NOT EXISTS webhook_notify_secret TEXT DEFAULT '';
        """,
    ),
    (
        46,
        "deploy_runs — audit trail for the post-merge auto-deployer (RAV-658)",
        # One row per finished deploy attempt. status vocabulary:
        #   deployed        → remote deploy + health checks all green
        #   deploy_failed   → the remote deploy script exited nonzero
        #   health_failed   → deploy succeeded but a post-deploy health URL failed
        #   checks_failed   → a required CI check concluded unsuccessfully
        #   checks_timeout  → required checks not green within the gate window
        #   error           → deployer-internal failure (check-run lookup, etc.)
        #   skipped         → repo not configured / deployer disabled
        # trigger: webhook (PR merged) | manual (POST /deploy/{repo}).
        """
        CREATE TABLE IF NOT EXISTS deploy_runs (
            id               TEXT PRIMARY KEY,
            repo             TEXT NOT NULL,
            status           TEXT NOT NULL,
            trigger          TEXT NOT NULL,
            detail           TEXT,
            pr_number        INTEGER,
            head_sha         TEXT,
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_deploy_runs_created ON deploy_runs (created_at DESC);
        """,
    ),
    (
        47,
        "drop deploy_runs — the RAV-658 auto-deployer is removed",
        # The post-merge auto-deployer is retired in favor of a registry-pull +
        # watchtower deploy on the target host. Migration 46 stays in the list
        # (ledger membership: existing DBs have it applied); this drop runs on
        # both old DBs (real drop) and fresh installs (46 creates, 47 drops —
        # harmless churn once, nothing thereafter).
        """
        DROP TABLE IF EXISTS deploy_runs;
        """,
    ),
    (
        48,
        "watcher briefing pack — verdict rationale + known-benign pattern registry (RAV-1138)",
        # rationale: the WHY behind every tick verdict (incl. no_issue), so
        # future ticks see reasoning, not just titles. watcher_known_patterns:
        # per-repo registry of patterns judged benign — deliberately NOT
        # age-windowed like watcher_runs, or recurring noise re-alerts after
        # the 7-day age-out. Rows are upserted by fingerprint; occurrences
        # tracks how often the agent re-confirmed the pattern.
        """
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS rationale TEXT;
        CREATE TABLE IF NOT EXISTS watcher_known_patterns (
            fingerprint TEXT PRIMARY KEY,
            repo        TEXT,
            summary     TEXT NOT NULL,
            first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            occurrences INTEGER NOT NULL DEFAULT 1
        );
        """,
    ),
    (
        49,
        "users table — SSO identity mirror + session control (RAV-1056 Phase A)",
        # JIT-populated at /auth/callback. Roles stay Auth0-owned; `tier` is a
        # cache of the last login's fold. `disabled` is an embry0-side deny
        # independent of Auth0 roles; `session_epoch` makes stateless session
        # cookies revocable (cookie carries the epoch it was minted under —
        # bumping the column force-logs-out that user). No FKs by design,
        # matching the audit_log philosophy.
        """
        CREATE TABLE IF NOT EXISTS users (
            id             TEXT PRIMARY KEY,
            auth0_sub      TEXT NOT NULL UNIQUE,
            email          TEXT NOT NULL,
            name           TEXT NOT NULL DEFAULT '',
            tier           TEXT NOT NULL DEFAULT 'none'
                           CHECK (tier IN ('none', 'operator', 'admin')),
            disabled       BOOLEAN NOT NULL DEFAULT FALSE,
            session_epoch  INTEGER NOT NULL DEFAULT 1,
            login_count    INTEGER NOT NULL DEFAULT 0,
            first_login_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (LOWER(email));
        """,
    ),
    (
        50,
        "api_keys table — per-user + service API keys (RAV-1056 Phase B)",
        # user_id NULL = service key (context-proxy, a chat relay). key_hash is
        # sha256 hex of the full token; the plaintext is shown exactly once
        # at mint. No FK to users by design (house audit philosophy) — a
        # deleted/disabled owner is enforced at validation time instead.
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id           TEXT PRIMARY KEY,
            user_id      TEXT,
            name         TEXT NOT NULL,
            key_hash     TEXT NOT NULL UNIQUE,
            prefix       TEXT NOT NULL,
            scope        TEXT NOT NULL CHECK (scope IN ('read', 'operate', 'admin')),
            created_by   TEXT NOT NULL DEFAULT '',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at   TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys (user_id);
        """,
    ),
    (
        51,
        "audit_log read-path indexes — actor/action filters (RAV-1056 Phase C)",
        # Composite (col, created_at DESC) serves both the global audit read
        # API's filters and its ORDER BY created_at DESC. No FKs, per house
        # audit philosophy (audit rows outlive every referenced entity).
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor  ON audit_log (actor, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log (action, created_at DESC);
        """,
    ),
    (
        52,
        "repos registry + repo_qa_configs — repo config lives in embry0 (RAV-1251)",
        # Owner directive 2026-08-06: no config files in target repos. repos is
        # the first-class registry (a repo exists in embry0 when it has a row);
        # repo_qa_configs holds the qa.yaml v2 document in Postgres, replacing
        # both the in-repo .embry0/qa.yaml and the Phase-1 file store as the
        # source of truth. The backfill registers every repo that was already
        # configured on a pre-registry surface, so existing deployments see
        # their fleet in the registry immediately. QA config rows themselves
        # are imported at startup (file store) / first run (in-repo file) by
        # the application layer, which can validate the yaml — SQL cannot.
        """
        CREATE TABLE IF NOT EXISTS repos (
            repo        TEXT PRIMARY KEY,
            active      BOOLEAN NOT NULL DEFAULT TRUE,
            notes       TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS repo_qa_configs (
            repo        TEXT PRIMARY KEY REFERENCES repos(repo) ON DELETE CASCADE,
            yaml_text   TEXT NOT NULL,
            updated_by  TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        INSERT INTO repos (repo)
        SELECT DISTINCT repo FROM repo_preferences
        UNION SELECT DISTINCT repo FROM repo_environment
        UNION SELECT DISTINCT repo FROM qa_workspace_provider_overrides
        ON CONFLICT (repo) DO NOTHING;
        """,
    ),
    (
        53,
        "repo_preferences.dev_checks — DB-native dev-check contract (RAV-1254)",
        # Owner directive extension: the per-repo pre-PR self-check commands
        # (formerly the target repo's .e0/dev.yaml, referenced only by prompt
        # text on the Linear-dispatch path) live in embry0. Shape: a JSONB
        # list of {"name": str, "paths": [prefix, ...], "command": str} —
        # the developer prompt renders "if you changed files under <paths>,
        # run <command>" for every code job regardless of dispatch path.
        """
        ALTER TABLE repo_preferences
            ADD COLUMN IF NOT EXISTS dev_checks JSONB NOT NULL DEFAULT '[]'::jsonb;
        COMMENT ON COLUMN repo_preferences.dev_checks IS
            'RAV-1254: [{"name", "paths": [prefix…], "command"}] pre-PR self-checks injected into the developer prompt. [] = none.';
        """,
    ),
    (
        54,
        "provider_config.disabled_models — per-model enablement (RAV-1297)",
        # Owner decision 2026-08-07: enablement extends the existing single
        # provider_config row, no new table. Stored as the DISABLED set so
        # the default is everything-enabled and models added to the catalog
        # later need no backfill. Tier-assigned models can never be disabled
        # (API-layer interlock).
        """
        ALTER TABLE provider_config
            ADD COLUMN IF NOT EXISTS disabled_models JSONB NOT NULL DEFAULT '[]'::jsonb;
        COMMENT ON COLUMN provider_config.disabled_models IS
            'RAV-1297: catalog model ids operators disabled for agent selection. [] = all enabled.';
        """,
    ),
    (
        55,
        "remove zombie agent rows resurrected by the ledger rename (RAV-1288)",
        # The athanor→embry0 ledger split re-ran migrations 1–5 against the
        # live DB on 2026-07-11; migration 2's ON CONFLICT DO NOTHING seed
        # re-inserted validator/output/reviewer — rows migration 6 had
        # removed (6+ were copied into the new ledger as already-applied and
        # never re-ran). Audit of the rest of 1–5: pure IF NOT EXISTS DDL
        # plus two single-row DO NOTHING config seeds that no-oped — these
        # three rows are the complete damage. reviewer is DELETED here, not
        # renamed as migration 6 did, because `review` already exists (the
        # rename would violate the PK). is_builtin guards a user-created
        # custom agent that happens to share a name. Runs idempotently.
        """
        DELETE FROM agent_definitions
        WHERE type IN ('validator', 'output', 'reviewer') AND is_builtin = true;
        """,
    ),
    (
        56,
        "drop qa_workspace_provider_overrides — override channel retired (RAV-1300)",
        # The per-repo workspace_provider override let operators tweak
        # affected_filter/apps_glob without committing to the target repo —
        # which mattered when qa.yaml lived in-repo. qa.yaml is DB-native
        # and dashboard-editable since RAV-1251/1253, so the config editor
        # IS that channel now; the table had zero rows in prod. Migration 31
        # (CREATE) and migration 52 (repos backfill SELECTing from this
        # table) stay untouched per the ledger-membership rule — on a fresh
        # DB they replay before this DROP, harmlessly create-then-drop.
        """
        DROP TABLE IF EXISTS qa_workspace_provider_overrides;
        """,
    ),
    (
        57,
        "bump builtin agent models to the Claude 5 family",
        # Migration 2 seeded triage/reviewer at claude-sonnet-4-6 and developer
        # at claude-opus-4-6; migration 6 renamed reviewer -> review without
        # touching its model; migration 14 bumped developer to claude-opus-4-7.
        # BUILTIN_SEED has since moved to the Claude 5 family, but nothing
        # carried that to the rows: seed_missing_builtin_agents is
        # insert-if-missing by design (builtin rows are operator-editable, and
        # POST /agents/{type}/reset is the restore path), so it never rewrites
        # an existing row. The result is that every install — including a fresh
        # one, since migrations run before the startup seeder — dispatches
        # triage, review, and developer on superseded models.
        #
        # Same guarded shape as migration 14: update IFF the model is still at
        # a value this repo seeded, so an operator who picked their own model
        # is never clobbered. Developer matches both 4-6 and 4-7 because an
        # install that predates migration 14 may still sit on 4-6.
        """
        UPDATE agent_definitions
        SET model = 'claude-sonnet-5', updated_at = NOW()
        WHERE type IN ('triage', 'review')
          AND model = 'claude-sonnet-4-6'
          AND is_builtin = true;

        UPDATE agent_definitions
        SET model = 'claude-opus-5', updated_at = NOW()
        WHERE type = 'developer'
          AND model IN ('claude-opus-4-6', 'claude-opus-4-7')
          AND is_builtin = true;
        """,
    ),
    (
        58,
        "integration_config — Linear credentials so the integration is Settings-manageable",
        # Linear was env-only: LINEAR_API_KEY / LINEAR_WEBHOOK_SECRET /
        # LINEAR_REPO_MAP had no DB counterpart and no Settings surface, so
        # the whole integration was unreachable from the dashboard while
        # GitHub, Slack, and Telegram were all editable there.
        #
        # No DEFAULT on these: the repository falls back to the LINEAR_* env
        # vars when a column is empty, and a column default would make an
        # unset field indistinguishable from a deliberately blank one.
        """
        ALTER TABLE integration_config ADD COLUMN IF NOT EXISTS linear_api_key TEXT DEFAULT '';
        ALTER TABLE integration_config ADD COLUMN IF NOT EXISTS linear_webhook_secret TEXT DEFAULT '';
        ALTER TABLE integration_config ADD COLUMN IF NOT EXISTS linear_repo_map TEXT DEFAULT '';

        COMMENT ON COLUMN integration_config.linear_api_key IS
            '#157: Linear personal API key. Empty falls back to LINEAR_API_KEY.';
        COMMENT ON COLUMN integration_config.linear_webhook_secret IS
            '#157: Linear webhook signing secret. Empty falls back to LINEAR_WEBHOOK_SECRET.';
        COMMENT ON COLUMN integration_config.linear_repo_map IS
            '#157: JSON project/team -> owner/repo map. Empty falls back to LINEAR_REPO_MAP.';
        """,
    ),
    (
        59,
        "repos.forge — which SCM host a repo lives on (#162)",
        # embry0 assumed one forge everywhere: `repos.repo` is a bare
        # "owner/name" string and every consumer parses it as GitHub. A second
        # forge needs the row to say where it lives, so clone URLs, API calls,
        # and credential proxies can route.
        #
        # `repo` stays the PRIMARY KEY rather than becoming a composite with
        # `forge`. A composite would mean changing repo_qa_configs' FK and
        # every repo-keyed table and call site — enormous blast radius to
        # solve a collision (the same path existing on two forges) that cannot
        # currently be created, since the string is already unique system-wide.
        # If that collision ever becomes real, the fix is a namespaced
        # identifier, not a wider key.
        #
        # DEFAULT 'github' backfills every existing row correctly: before this
        # migration, GitHub was the only possibility.
        """
        ALTER TABLE repos ADD COLUMN IF NOT EXISTS forge TEXT NOT NULL DEFAULT 'github';

        COMMENT ON COLUMN repos.forge IS
            '#162: SCM host for this repo — github | gitlab. Drives clone URL, API base, and credential proxy selection.';

        CREATE INDEX IF NOT EXISTS idx_repos_forge ON repos (forge);
        """,
    ),
    (
        60,
        "issues — generic tracker reference columns (#165)",
        # GitHub got first-class columns (github_number/github_url); Linear was
        # bolted on later as a parallel pair (linear_identifier/linear_issue_id).
        # A third bespoke pair for GitLab is exactly the trajectory #161 exists
        # to stop, so GitLab uses generic columns from the start instead.
        #
        # github_* and linear_* are deliberately left untouched. #161 migrates
        # them onto these same columns and drops the parallel pairs; doing that
        # now would mean rewriting the Linear service, which is being edited
        # concurrently on another machine.
        #
        # tracker_key is TEXT, not INTEGER: GitHub numbers and GitLab iids are
        # integers but Linear identifiers are "ENG-123", and #161 has to fold
        # all three into this one column.
        """
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS tracker      TEXT;
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS tracker_key  TEXT;
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS tracker_url  TEXT;

        COMMENT ON COLUMN issues.tracker IS
            '#165: forge/tracker owning this issue — gitlab today; github and linear fold in via #161.';
        COMMENT ON COLUMN issues.tracker_key IS
            '#165: tracker-native id, scoped to repo. GitLab issue iid; later GitHub number / Linear identifier.';
        COMMENT ON COLUMN issues.tracker_url IS
            '#165: canonical web URL of the tracker issue.';

        -- Partial unique index: only rows carrying a tracker ref participate,
        -- so existing github_*/linear_* rows are unaffected.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_tracker_ref
            ON issues (tracker, repo, tracker_key)
            WHERE tracker IS NOT NULL AND tracker_key IS NOT NULL;
        """,
    ),
    (
        61,
        "issues — fold github_*/linear_* refs onto the generic tracker columns (#161)",
        # The issues table was column-per-platform: GitHub refs first-class
        # since migration 13, Linear bolted on as a parallel pair in 40,
        # GitLab already generic since 60. This is the collapse migration 60
        # promised: backfill the per-platform refs onto tracker/tracker_key/
        # tracker_url (+ the new tracker_native_id for Linear's UUID), then
        # drop the parallel pairs and their indexes so a fourth tracker can
        # never grow a fourth column family.
        #
        # github_sync_enabled / github_synced_at are NOT refs — they gate the
        # GitHub-only outbound push feature (push_create/push_update) — and
        # deliberately stay first-class columns.
        #
        # Ordering note: the github backfill writes tracker_key as
        # github_number::text; the WHERE tracker IS NULL guard makes both
        # backfills idempotent and keeps them off rows that already carry a
        # generic ref (GitLab rows, or rows from a partially-applied run).
        """
        ALTER TABLE issues ADD COLUMN IF NOT EXISTS tracker_native_id TEXT;

        COMMENT ON COLUMN issues.tracker_native_id IS
            '#161: tracker-internal id when it differs from tracker_key — Linear''s UUID (commentCreate wants it). NULL for GitHub/GitLab.';

        UPDATE issues
           SET tracker = 'github',
               tracker_key = github_number::text,
               tracker_url = github_url
         WHERE github_number IS NOT NULL AND tracker IS NULL;

        UPDATE issues
           SET tracker = 'linear',
               tracker_key = linear_identifier,
               tracker_native_id = linear_issue_id
         WHERE linear_identifier IS NOT NULL AND tracker IS NULL;

        DROP INDEX IF EXISTS idx_issues_github;
        DROP INDEX IF EXISTS idx_issues_linear_identifier;

        ALTER TABLE issues DROP COLUMN IF EXISTS github_number;
        ALTER TABLE issues DROP COLUMN IF EXISTS github_url;
        ALTER TABLE issues DROP COLUMN IF EXISTS linear_identifier;
        ALTER TABLE issues DROP COLUMN IF EXISTS linear_issue_id;

        COMMENT ON COLUMN issues.tracker IS
            '#161: tracker owning this issue — github | gitlab | linear. NULL for dashboard/API-created issues with no tracker ref.';
        COMMENT ON COLUMN issues.tracker_key IS
            '#161: tracker-native human key, scoped to repo. GitHub number / GitLab iid (digits), Linear identifier (RAV-1234).';
        """,
    ),
    (
        62,
        "issue_inputs — record which GitHub comment rendered each question (#253)",
        # "/answer 2:" used to mean "the 2nd row of the CURRENT pending list",
        # so an answer arriving from the dashboard/Telegram/MCP between the
        # comment being written and the webhook landing silently shifted the
        # target. Persisting the render-time ordering makes N mean what the
        # author read; rows with no mapping (pre-#253 comments) resolve to
        # nothing rather than to the wrong question.
        """
        ALTER TABLE issue_inputs
            ADD COLUMN IF NOT EXISTS github_comment_id BIGINT;
        ALTER TABLE issue_inputs
            ADD COLUMN IF NOT EXISTS github_comment_seq INTEGER;

        CREATE INDEX IF NOT EXISTS idx_issue_inputs_github_comment
            ON issue_inputs (issue_id, github_comment_id, github_comment_seq)
            WHERE github_comment_id IS NOT NULL;

        COMMENT ON COLUMN issue_inputs.github_comment_id IS
            '#253: id of the GitHub issue comment that rendered this question. NULL for questions never rendered on GitHub.';
        COMMENT ON COLUMN issue_inputs.github_comment_seq IS
            '#253: 1-based position of this question within that comment. /answer N: resolves against this, never the live pending list.';
        """,
    ),
    (
        63,
        "jobs — dispatched_by: the actor that triggered the job (#246)",
        # The dispatch rate limit (layer 3) used to bucket on
        # issues.created_by, which is a provenance constant ("webhook-label",
        # "linear-webhook", "triage"), never a person — so the fleet shared
        # one hourly budget and triage's own sub-issues throttled each other.
        # This column carries the real actor where one exists: the webhook
        # sender, or the API principal. NULL when nobody identifiable asked
        # (triage sub-issues, the log watcher), and the gate skips the limit
        # for NULL rather than lumping those rows together.
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dispatched_by TEXT;

        CREATE INDEX IF NOT EXISTS idx_jobs_dispatched_by_created_at
            ON jobs (dispatched_by, created_at)
            WHERE dispatched_by IS NOT NULL;

        COMMENT ON COLUMN jobs.dispatched_by IS
            '#246: the actor that triggered this job (webhook sender login / API principal). NULL when no identifiable actor asked.';
        """,
    ),
    (
        64,
        "repo_preferences.base_branch — the repo's integration branch (Spec 1A §3.7)",
        # One value, three consumers that each used to guess separately: the
        # init clone fetched `main:main`, the MR/PR fallback targeted the
        # forge default, and the review/QA diff walked origin/HEAD. A repo
        # whose work lands on `dev` needed all three to agree and none of
        # them could be told. NULL keeps the existing behaviour (ask the
        # forge for its default branch, then fall back to main), so this is
        # inert until an operator sets it.
        """
        ALTER TABLE repo_preferences
            ADD COLUMN IF NOT EXISTS base_branch TEXT;

        COMMENT ON COLUMN repo_preferences.base_branch IS
            'Spec 1A 3.7: branch jobs clone, diff against and target with the MR/PR. NULL = ask the forge for its default branch, then fall back to main.';
        """,
    ),
    (
        65,
        "no-op: retired one deployment's repo URL rewrite (#277, #527)",
        # Version 65 used to rewrite stored PR/issue URLs after one
        # deployment's repo rename (#277). A rename is that operator's
        # configuration, not product history, so the rewrite is gone (#527)
        # and the entry stays only to keep the numbers contiguous: the runner
        # keys on the integer. A database that applied the old 65 has its
        # ledger row and never runs this again; a new one records 65 and
        # moves on. Nothing replaces it: merge sync and the healing ledger
        # look stored rows up under every spelling of a repo in REPO_RENAMES
        # (storage/repo_renames.py), so no stored URL needs rewriting.
        "SELECT 1;",
    ),
    (
        66,
        "watcher_runs — tick diagnostics: briefing, retry, window gap, line cap (SH-P2.4)",
        # briefing_failures: which briefing sections fell back to
        # "(unavailable)" on this tick — the analysis stays fail-open, but a
        # verdict reached on a degraded briefing must be tellable from one
        # reached on a full one. retried_job_id + analysis_cost_usd: an
        # unparseable deliverable is re-analysed once; job_id keeps pointing
        # at the attempt whose output was used, retried_job_id at the discarded
        # first attempt, and the cost column sums every attempt so the retry's
        # spend is visible without joining jobs. NULL cost = no analysis ran.
        # No column is needed for the contiguous query window itself: it is
        # derived from window_end of the last analysed row. unanalysed_seconds
        # is the time between that end and this tick's window_start that the
        # max look-back left out (0 = contiguous) — on an analysed row those
        # logs are lost for good. log_lines_truncated: the excerpt hit
        # WATCHER_MAX_LOG_LINES, so Loki (direction=backward) dropped the
        # OLDEST matches; windows are contiguous in time, not in lines.
        """
        ALTER TABLE watcher_runs
            ADD COLUMN IF NOT EXISTS briefing_failures TEXT[] NOT NULL DEFAULT '{}';
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS retried_job_id TEXT;
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS analysis_cost_usd DOUBLE PRECISION;
        ALTER TABLE watcher_runs
            ADD COLUMN IF NOT EXISTS unanalysed_seconds INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE watcher_runs
            ADD COLUMN IF NOT EXISTS log_lines_truncated BOOLEAN NOT NULL DEFAULT FALSE;
        """,
    ),
    (
        67,
        "tracker dispatch ledgers — once-per-reason notices + dispatch failure backoff (#284)",
        # Both are keyed on the tracker's own key rather than issues.id: an
        # unmapped ticket never gets an embry0 row to hang state on.
        #
        # notices: a ticket that carries the trigger label but is not
        # dispatched gets a comment saying why. The webhook fires on every
        # ticket edit and the reconciler sweeps on an interval, so without a
        # ledger the same sentence would be posted over and over.
        #
        # failures: a dispatch that raises leaves no job row to count, so the
        # reconciler would retry it on every sweep forever. The count and the
        # timestamp drive its backoff and attempt cap across restarts.
        """
        CREATE TABLE IF NOT EXISTS tracker_dispatch_notices (
            tracker     TEXT NOT NULL,
            tracker_key TEXT NOT NULL,
            reason      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tracker, tracker_key, reason)
        );

        COMMENT ON TABLE tracker_dispatch_notices IS
            '#284: ledger of "embry0 did not dispatch this ticket because <reason>" comments - at most one per (ticket, reason) until the next successful dispatch.';

        CREATE TABLE IF NOT EXISTS tracker_dispatch_failures (
            tracker        TEXT NOT NULL,
            tracker_key    TEXT NOT NULL,
            failures       INTEGER NOT NULL DEFAULT 0,
            last_failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tracker, tracker_key)
        );

        COMMENT ON TABLE tracker_dispatch_failures IS
            '#284: dispatches that raised before a job row existed - drives the reconciler''s backoff and attempt cap; cleared by a successful dispatch.';
        """,
    ),
    (
        68,
        "watcher_runs.released_at — release a cap slot without erasing the ticket id (#278)",
        # Reconcile used to free a dead draft's cap slot by NULLing
        # linear_issue_identifier, which destroyed the proposal → ticket link
        # (9 of the first 30 proposals). The slot is now released by stamping
        # released_at; the open-proposal predicate excludes stamped rows.
        # Rows NULLed before this migration stay excluded by the predicate's
        # existing IS NOT NULL clause — their ids are unrecoverable here.
        """
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS released_at TIMESTAMPTZ;

        COMMENT ON COLUMN watcher_runs.released_at IS
            '#278: when reconcile released this draft''s cap slot (ticket cancelled/done/missing). The ticket id is kept.';
        """,
    ),
    (
        69,
        "healing_cases — one lifecycle ledger per finding (#278)",
        # The record that answers "what happened to this finding" with a join
        # instead of a title LIKE:
        #   signature → watcher_run → tracker → issue → jobs[] → PRs[]
        #             → merge_sha → deploy marker → healed verdict
        # Vocabulary is owned by embry0/services/healing/lifecycle.py; a unit
        # test holds the CHECK lists here equal to those enums.
        #   * stage / resolution_path / healed_verdict / PR state are closed
        #     vocabularies the state machine depends on → CHECK.
        #   * origin is deliberately NOT a CHECK: it is an open list of
        #     detection sources (watcher, human, webhook, then alert and ci
        #     with P3.8, more after), validated by the Origin enum in the one
        #     write path. A CHECK would cost a migration per new source and
        #     protect nothing the service does not already enforce.
        #
        # Foreign keys are real where the referenced table exists. Links are
        # ON DELETE SET NULL (a ledger row outlives what it points at); the
        # child tables CASCADE with their case.
        #   * signature_id has no FK yet: log_signatures arrives with #289,
        #     which adds the constraint. `signature` carries the free-text /
        #     LogQL form until then.
        #   * repo is plain TEXT, like issues.repo and jobs.repo: a watched
        #     repo need not be in the repos registry.
        #   * jobs and PRs are many per case (retries, superseded PRs) →
        #     child tables. A case has no "current PR" column to drift.
        #   * deploy_* is the ANCHOR for confirm-healed: the first deploy that
        #     carried the merge. Later deploys are events only.
        #   * "a fix is deployed" == merge_sha set AND deployed_at >= merged_at,
        #     NEVER a stage comparison: a case can be verified or closed with
        #     no fix (self_resolved, non_code). See lifecycle.fix_deployed.
        #   * verdict_evidence holds counts and queries, never raw log lines:
        #     the API serves it. The service caps it and the free-text columns.
        # <stage>_at columns hold when the case last entered that stage;
        # healing_case_events keeps the full history — each row's
        # detail.updates is the exact column change, so the case row can be
        # rebuilt from its events.
        """
        CREATE TABLE IF NOT EXISTS healing_cases (
            id                  TEXT PRIMARY KEY,
            repo                TEXT NOT NULL,
            title               TEXT NOT NULL DEFAULT '',
            origin              TEXT NOT NULL,
            stage               TEXT NOT NULL DEFAULT 'detected'
                CHECK (stage IN ('detected', 'tracked', 'accepted', 'pr_open',
                                 'merged', 'deployed', 'verified', 'closed')),
            resolution_path     TEXT
                CHECK (resolution_path IN ('embry0', 'out_of_band', 'cancelled',
                                           'non_code', 'self_resolved')),
            severity            TEXT,
            fixability          TEXT,
            signature           TEXT,
            signature_id        TEXT,
            fingerprint         TEXT,
            occurrence_count    INTEGER NOT NULL DEFAULT 1,
            last_seen_at        TIMESTAMPTZ,
            watcher_run_id      TEXT REFERENCES watcher_runs(id) ON DELETE SET NULL,
            tracker             TEXT,
            tracker_key         TEXT,
            tracker_url         TEXT,
            issue_id            TEXT REFERENCES issues(id) ON DELETE SET NULL,
            merge_sha           TEXT,
            deploy_sha          TEXT,
            deploy_marker_at    TIMESTAMPTZ,
            deploy_services     TEXT[],
            healed_verdict      TEXT NOT NULL DEFAULT 'pending'
                CHECK (healed_verdict IN ('pending', 'healed', 'not_healed',
                                          'inconclusive', 'regressed')),
            verdict_evidence    JSONB,
            verdict_checked_at  TIMESTAMPTZ,
            parent_case_id      TEXT REFERENCES healing_cases(id) ON DELETE SET NULL,
            caused_regression_case_id TEXT REFERENCES healing_cases(id) ON DELETE SET NULL,
            note                TEXT,
            detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tracked_at          TIMESTAMPTZ,
            accepted_at         TIMESTAMPTZ,
            pr_open_at          TIMESTAMPTZ,
            merged_at           TIMESTAMPTZ,
            deployed_at         TIMESTAMPTZ,
            verified_at         TIMESTAMPTZ,
            closed_at           TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT healing_cases_tracker_pair
                CHECK ((tracker IS NULL) = (tracker_key IS NULL))
        );

        -- One case per watcher proposal, and one OPEN case per tracker ticket:
        -- these make open_case and the backfill idempotent. The tracker index
        -- is partial on purpose — a closed case must not swallow the
        -- regression, reopened ticket or reused ticket that follows it.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_cases_watcher_run
            ON healing_cases (watcher_run_id) WHERE watcher_run_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_cases_open_tracker
            ON healing_cases (tracker, tracker_key)
            WHERE tracker IS NOT NULL AND stage <> 'closed';
        CREATE INDEX IF NOT EXISTS idx_healing_cases_tracker
            ON healing_cases (tracker, tracker_key, created_at DESC) WHERE tracker IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_healing_cases_list
            ON healing_cases (created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_healing_cases_repo_stage
            ON healing_cases (repo, stage);
        CREATE INDEX IF NOT EXISTS idx_healing_cases_issue
            ON healing_cases (issue_id) WHERE issue_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_healing_cases_signature_id
            ON healing_cases (signature_id) WHERE signature_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS healing_case_jobs (
            case_id      TEXT NOT NULL REFERENCES healing_cases(id) ON DELETE CASCADE,
            job_id       TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
            attached_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (case_id, job_id)
        );
        CREATE INDEX IF NOT EXISTS idx_healing_case_jobs_job ON healing_case_jobs (job_id);

        -- Every PR a case ever had, each with its own outcome. url is unique:
        -- a PR belongs to one case, and any of them finds its case.
        -- head_sha_at is the ordering guard: an older redelivered push never
        -- rewinds head_sha.
        CREATE TABLE IF NOT EXISTS healing_case_prs (
            id           BIGSERIAL PRIMARY KEY,
            case_id      TEXT NOT NULL REFERENCES healing_cases(id) ON DELETE CASCADE,
            url          TEXT NOT NULL UNIQUE,
            number       INTEGER,
            head_sha     TEXT,
            head_sha_at  TIMESTAMPTZ,
            state        TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open', 'merged', 'closed')),
            merge_sha    TEXT,
            job_id       TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
            opened_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            merged_at    TIMESTAMPTZ,
            closed_at    TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_healing_case_prs_case ON healing_case_prs (case_id, id);

        -- event_key is the caller's idempotency key (a forge delivery id):
        -- a redelivery carrying a key the case has seen is dropped whole.
        CREATE TABLE IF NOT EXISTS healing_case_events (
            id           BIGSERIAL PRIMARY KEY,
            case_id      TEXT NOT NULL REFERENCES healing_cases(id) ON DELETE CASCADE,
            event        TEXT NOT NULL,
            event_key    TEXT,
            from_stage   TEXT,
            to_stage     TEXT NOT NULL,
            detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_healing_case_events_case
            ON healing_case_events (case_id, id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_case_events_key
            ON healing_case_events (case_id, event_key) WHERE event_key IS NOT NULL;
        """,
    ),
    (
        70,
        "watcher_tick_attempts — a tick leaves a trace when it STARTS (#335)",
        # The watcher loop schedules its first tick after a restart from what
        # is recorded, not from process start. watcher_runs rows are written
        # when a tick ENDS, so a tick killed mid-analysis (a deploy, a crash
        # loop) left nothing — and an "overdue" rule on runs alone would start
        # a fresh analysis job on every restart. One row, upserted before each
        # scheduled tick; GREATEST keeps a late writer from rewinding it. Kept
        # out of watcher_runs on purpose: that table is the audit trail of
        # finished ticks and every reader of it assumes one row per tick.
        """
        CREATE TABLE IF NOT EXISTS watcher_tick_attempts (
            id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            attempted_at TIMESTAMPTZ NOT NULL
        );

        COMMENT ON TABLE watcher_tick_attempts IS
            '#335: single row — when the watcher loop last STARTED a tick. Read with MAX(watcher_runs.created_at) to schedule the next one across restarts.';
        """,
    ),
    (
        71,
        "traces.error_code / stderr_tail; backfill unambiguous job error codes (#281)",
        # Two halves, one transaction.
        #
        # 1. traces gains the per-RUN failure record: the runner's diagnosis
        #    and the redacted, 8 KiB-capped end of the agent's stderr. Per run
        #    rather than per job because a recovered attempt's cause is
        #    otherwise lost, and 27 developer exit-1 runs had no recorded
        #    cause at all. NULL for successful runs and for every row written
        #    before this migration.
        #
        # 2. jobs.error_code is backfilled for historical failed rows that
        #    carry ERR_UNKNOWN or no code, ONLY where error_message matches
        #    exactly one rule below. The rules are _M71_BACKFILL_RULES, pinned
        #    here (a migration is history; the live classifier may move on). A
        #    row whose message matches rules for two different codes is left
        #    alone, as is anything matching none: a wrong code is worse than
        #    an honest ERR_UNKNOWN. Idempotent — a coded row no longer matches
        #    the WHERE clause.
        #
        # jobs.updated_at is preserved: relabelling a failure is not an event
        # in the life of a job that ended weeks ago, and merge-sync / the
        # reaper read that column. trg_jobs_updated_at (migration 7)
        # overwrites NEW.updated_at unconditionally, so "SET updated_at =
        # updated_at" cannot hold it; the trigger is disabled around the
        # backfill instead. That is safe because DISABLE TRIGGER is
        # transactional DDL and the runner gives every migration its own
        # transaction: a failure rolls the disable back, and the SHARE ROW
        # EXCLUSIVE lock it takes keeps any other writer from updating jobs
        # trigger-less in the meantime (they wait while it runs).
        """
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS error_code TEXT;
        ALTER TABLE traces ADD COLUMN IF NOT EXISTS stderr_tail TEXT;
        COMMENT ON COLUMN traces.error_code IS
            '#281: ErrorCode value for an errored agent run; NULL on success.';
        COMMENT ON COLUMN traces.stderr_tail IS
            '#281: redacted tail (<= 8 KiB) of the agent runner stderr for an errored run. Dashboard/API only - never posted to a tracker.';

        ALTER TABLE jobs DISABLE TRIGGER trg_jobs_updated_at;
        WITH rules(pattern, code) AS (
            VALUES
        """
        + _m71_rules_sql()
        + """
        ),
        matched AS (
            SELECT j.job_id, MIN(r.code) AS code
            FROM jobs j
            JOIN rules r ON j.error_message ~ r.pattern
            WHERE j.status = 'failed'
              AND (j.error_code IS NULL OR j.error_code = 'ERR_UNKNOWN')
            GROUP BY j.job_id
            HAVING COUNT(DISTINCT r.code) = 1
        )
        UPDATE jobs SET error_code = m.code
        FROM matched m
        WHERE jobs.job_id = m.job_id;
        ALTER TABLE jobs ENABLE TRIGGER trg_jobs_updated_at;
        """,
    ),
    (
        72,
        "verification: per-repo config + one durable result per job (#298, #299)",
        # repo_preferences.verification — the gate's test commands and the
        # tier path rules (shape: embry0/workflows/verification/config.py).
        # NULL = not configured: the gate reports not_configured and never
        # blocks. Repo config lives in embry0, never in the target repo.
        #
        # job_verifications — what the orchestrator-run gate and the tier
        # classifier decided for a job. A table, not a jobs column: auto-merge
        # (#310) has to look a verdict up by the PR's CURRENT head sha (a
        # verdict for an older head is stale), the report carries output tails
        # that do not belong in every jobs list query, and the jobs row is
        # rewritten by many writers while this row has exactly one. The
        # vocabularies (tier, gate_verdict, browser_qa) are validated in code,
        # not by CHECKs, so a later tier or verdict needs no migration.
        """
        ALTER TABLE repo_preferences ADD COLUMN IF NOT EXISTS verification JSONB;
        COMMENT ON COLUMN repo_preferences.verification IS
            '#298/#299: {"test_targets": [{"name","paths","command","timeout_seconds"}], "base_run", "head_reruns", "total_timeout_seconds", "tiers": {"frontend_paths","backend_paths","neutral_paths"}}. NULL = not configured.';

        CREATE TABLE IF NOT EXISTS job_verifications (
            job_id           TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
            repo             TEXT NOT NULL DEFAULT '',
            mode             TEXT NOT NULL DEFAULT 'on',
            tier             TEXT NOT NULL,
            gate_verdict     TEXT NOT NULL,
            blocking         BOOLEAN NOT NULL DEFAULT FALSE,
            browser_qa       TEXT NOT NULL,
            human_merge_only BOOLEAN NOT NULL DEFAULT FALSE,
            base_sha         TEXT NOT NULL DEFAULT '',
            head_sha         TEXT NOT NULL DEFAULT '',
            report           JSONB NOT NULL,
            published_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_job_verifications_repo_head
            ON job_verifications (repo, head_sha) WHERE head_sha <> '';
        """,
    ),
    (
        73,
        "jobs.delivery_state — what became of the job's pull request, apart from status (#279, #280)",
        # status says how the JOB ended; delivery_state says what happened to
        # the PULL REQUEST it produced (none / pr_open / merged / closed). A
        # job can end `failed` and its PR still merge — 18 such rows on
        # 2026-09-20 — so the two are separate columns maintained by one
        # writer (services/merge_sync). delivery_state_at is the FORGE's time
        # of that state (merged_at / closed_at / updated_at), which is what
        # orders redelivered and out-of-order webhooks; NULL means "derived
        # from status by this migration, forge time unknown" and yields to
        # any dated fact.
        #
        # The seed below is what the DB can already prove: pr_merged /
        # pr_closed were only ever written by the PR webhook, and a stored
        # pr_url means a PR was opened. Merge sync's first sweep corrects the
        # rest against GitHub. Idempotent (only rows still `none` are
        # touched); jobs.updated_at is preserved the same way migration 71
        # does it, for the same reason — see the comment there.
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS delivery_state TEXT NOT NULL DEFAULT 'none';
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS delivery_state_at TIMESTAMPTZ;
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jobs_delivery_state_check') THEN
                ALTER TABLE jobs ADD CONSTRAINT jobs_delivery_state_check
                    CHECK (delivery_state IN ('none', 'pr_open', 'merged', 'closed'));
            END IF;
        END $$;

        ALTER TABLE jobs DISABLE TRIGGER trg_jobs_updated_at;
        UPDATE jobs
           SET delivery_state = CASE
                   WHEN status = 'pr_merged' THEN 'merged'
                   WHEN status = 'pr_closed' THEN 'closed'
                   ELSE 'pr_open'
               END
         WHERE delivery_state = 'none'
           AND (status IN ('pr_merged', 'pr_closed') OR COALESCE(pr_url, '') <> '');
        ALTER TABLE jobs ENABLE TRIGGER trg_jobs_updated_at;

        CREATE INDEX IF NOT EXISTS idx_jobs_delivery_unsettled
            ON jobs (created_at) WHERE delivery_state = 'pr_open';
        """,
    ),
    (
        74,
        "watcher_runs.analysed_under_full_cap — bound the analyses a full cap no longer bounds (#283)",
        # Before #283 a full draft cap ended the tick before Loki was even
        # queried, so a full-cap stretch cost nothing: the draft cap WAS the
        # de-facto daily spend cap. Now such a tick analyses (that is the
        # point — 682 of 1,339 ticks were blind), so the analyses it pays for
        # need their own durable daily cap. Only a row flagged here counts
        # against it, so an ordinary tick's spend is untouched by the limit.
        # Additive with a default: existing rows read FALSE, which is true of
        # every tick that ran before this column existed.
        """
        ALTER TABLE watcher_runs
            ADD COLUMN IF NOT EXISTS analysed_under_full_cap BOOLEAN NOT NULL DEFAULT FALSE;

        COMMENT ON COLUMN watcher_runs.analysed_under_full_cap IS
            '#283: this tick paid for an analysis while the draft cap was full. Counted by '
            'WatcherRunsRepository.full_cap_analyses_since against WATCHER_MAX_FULL_CAP_ANALYSES_PER_DAY.';

        CREATE INDEX IF NOT EXISTS idx_watcher_runs_full_cap_analyses
            ON watcher_runs (created_at DESC) WHERE analysed_under_full_cap;
        """,
    ),
    (
        75,
        "qa_image_tags — base image digest, one active row per repo (#334)",
        # A pre-built QA image is a `docker commit` of a QA sandbox, so it
        # carries the sandbox runner of the day it was built. Recording which
        # base image it was committed from lets selection refuse one whose
        # base has since been rebuilt.
        #   * Columns, not metadata keys: `record` replaces metadata wholesale
        #     on upsert, the staleness summary filters on the value, and NULL
        #     is an honest "unknown" for every row built before this.
        #   * base_image is the name the builder container was started from
        #     (registry-qualified); base_image_digest is that image's ID in
        #     the daemon that runs sandboxes. Both or neither.
        #   * No backfill: the digest of a past build cannot be recovered
        #     from the row.
        #
        # One active row per repo becomes a database invariant. `record`
        # never deactivated the previous row, so production holds two active
        # rows for one repo; keep the newest per repo (id breaks an exact
        # built_at tie) and deactivate the rest before the index is built.
        # Nothing is deleted and no other column is touched. The table has no
        # updated_at and no trigger, so historical rows keep their timestamps.
        """
        ALTER TABLE qa_image_tags ADD COLUMN IF NOT EXISTS base_image TEXT;
        ALTER TABLE qa_image_tags ADD COLUMN IF NOT EXISTS base_image_digest TEXT;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conname = 'ck_qa_image_tags_base_pair'
                   AND conrelid = 'qa_image_tags'::regclass
            ) THEN
                ALTER TABLE qa_image_tags ADD CONSTRAINT ck_qa_image_tags_base_pair
                    CHECK ((base_image IS NULL) = (base_image_digest IS NULL));
            END IF;
        END $$;

        UPDATE qa_image_tags AS old
           SET is_active = false
         WHERE old.is_active
           AND EXISTS (
               SELECT 1 FROM qa_image_tags AS newer
                WHERE newer.repo = old.repo
                  AND newer.is_active
                  AND (newer.built_at, newer.id) > (old.built_at, old.id)
           );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_qa_image_tags_one_active_per_repo
            ON qa_image_tags (repo) WHERE is_active;
        """,
    ),
    (
        76,
        "log_signatures — deterministic log clustering memory (#289, SH-P3.1)",
        # One row per (signature, repo): the durable memory behind "is this new?"
        # and "is this getting worse?", which no tick could answer while the
        # watcher just re-read the newest N raw lines each hour.
        #
        # `repo` is in the primary key, not merely a column. The watcher reads one
        # repo today, but a signature's rate is meaningless pooled across repos —
        # two services emitting the same Spring exception are two faults. Making
        # that a key from the start avoids a painful re-key later. '' means
        # unscoped, since a NULL cannot sit in a primary key.
        #
        # No `rate` column on purpose. A stored rate is wrong the moment the next
        # line lands, and #312 needs the rate *before* a fix shipped — which is
        # derivable from occurrence_count over the first/last span. The raw facts
        # are stored; the rate is computed on read.
        """
        CREATE TABLE IF NOT EXISTS log_signatures (
            signature_id      TEXT NOT NULL,
            repo              TEXT NOT NULL DEFAULT '',
            template          TEXT NOT NULL,
            first_seen_at     TIMESTAMPTZ NOT NULL,
            last_seen_at      TIMESTAMPTZ NOT NULL,
            occurrence_count  BIGINT NOT NULL DEFAULT 0,
            ticks_seen        INTEGER NOT NULL DEFAULT 0,
            last_window_count INTEGER NOT NULL DEFAULT 0,
            services          TEXT[] NOT NULL DEFAULT '{}',
            sample_lines      JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (signature_id, repo)
        );

        -- "what is loudest / most recent for this repo" — the two reads the
        -- ranked table and the #312 verdict both make.
        CREATE INDEX IF NOT EXISTS idx_log_signatures_repo_last_seen
            ON log_signatures (repo, last_seen_at DESC);
        CREATE INDEX IF NOT EXISTS idx_log_signatures_repo_count
            ON log_signatures (repo, occurrence_count DESC);
        """,
    ),
    (
        77,
        "digest_deliveries — the daily embry0 summary is sent once per day",
        # One row per UTC day. The digest loop claims the day with
        # INSERT … ON CONFLICT DO NOTHING RETURNING before it sends, so exactly
        # one sender wins however many restarts, replicas or retries happen in
        # that day — the same claim-once idiom as tracker_dispatch_notices.
        # `payload` keeps what was sent, so a digest that reads wrong can be
        # checked against the numbers it was built from.
        """
        CREATE TABLE IF NOT EXISTS digest_deliveries (
            day        DATE PRIMARY KEY,
            claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            sent_at    TIMESTAMPTZ,
            payload    JSONB
        );
        """,
    ),
    (
        78,
        "merge_approvals + the `merge` key scope — 'merge this PR?' from the Teams chat",
        # One row per ask. The ask pins the PR's head sha; a decision is only
        # acted on for that exact commit, so a push after the ask turns the row
        # `stale` and a fresh ask replaces it rather than merging code nobody
        # looked at.
        #
        # `pending` is the only answerable state that can also be refused: a
        # decision refused for red or running CI leaves the row pending (with
        # the reason in `detail`) so it can be answered again after a re-run.
        # `failed` is a merge call GitHub rejected and may be approved again.
        # `approved` + dry_run is the recorded "would merge".
        #
        # The partial unique index is the one-ask-per-PR rule: a second ask for
        # a PR that already has a pending one is a no-op, not a duplicate.
        """
        CREATE TABLE IF NOT EXISTS merge_approvals (
            id          TEXT PRIMARY KEY,
            repo        TEXT NOT NULL,
            pr_number   INTEGER NOT NULL,
            pr_url      TEXT NOT NULL,
            head_sha    TEXT NOT NULL,
            job_id      TEXT,
            case_id     TEXT,
            status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'merged', 'failed', 'stale')),
            dry_run     BOOLEAN NOT NULL DEFAULT TRUE,
            decided_by  TEXT,
            decided_at  TIMESTAMPTZ,
            chat_id     TEXT,
            detail      TEXT,
            merge_sha   TEXT,
            ask         JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS uq_merge_approvals_one_pending_per_pr
            ON merge_approvals (repo, pr_number) WHERE status = 'pending';
        CREATE INDEX IF NOT EXISTS idx_merge_approvals_job ON merge_approvals (job_id);

        -- The `merge` key scope: what Hermes holds to relay a decision. It may
        -- read, decide merge approvals and answer agent questions, nothing else
        -- (enforced in embry0/api/deps.py). Drop-and-add keeps this re-runnable.
        ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_scope_check;
        ALTER TABLE api_keys ADD CONSTRAINT api_keys_scope_check
            CHECK (scope IN ('read', 'operate', 'admin', 'merge'));
        """,
    ),
    (
        79,
        "merge_approvals — post the ask once CI settles; asks retire themselves",
        # An ask is now recorded when the job finishes but posted to the chat
        # only once the PR's CI has settled (or a cap passed), so its
        # recommendation reads real CI instead of "pending". `posted_at` is
        # that moment; NULL = recorded, not yet shown to anyone.
        #
        # Nobody has to answer "don't merge": an ask whose PR was merged or
        # closed on GitHub becomes `closed`, and one left untouched past the
        # expiry becomes `expired`. Both carry the reason in `detail`.
        #
        # Every row before this migration was posted when it was created.
        """
        ALTER TABLE merge_approvals ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ;
        UPDATE merge_approvals SET posted_at = created_at WHERE posted_at IS NULL;

        ALTER TABLE merge_approvals DROP CONSTRAINT IF EXISTS merge_approvals_status_check;
        ALTER TABLE merge_approvals ADD CONSTRAINT merge_approvals_status_check
            CHECK (status IN ('pending', 'approved', 'rejected', 'merged', 'failed', 'stale', 'closed', 'expired'));

        -- The sweep's two reads: asks waiting to be posted, and posted asks to retire.
        CREATE INDEX IF NOT EXISTS idx_merge_approvals_pending_posted
            ON merge_approvals (posted_at) WHERE status = 'pending';
        """,
    ),
    (
        80,
        "merge_approvals.ask — unwrap the double-encoded JSON strings",
        # 78 and 79 passed json.dumps(ask) to a pool whose JSONB codec encodes
        # again, so every `ask` is a JSON *string* holding the object and
        # `ask->>'title'` is NULL. Unwrap in place; idempotent (objects are
        # left alone).
        """
        UPDATE merge_approvals SET ask = (ask #>> '{}')::jsonb
         WHERE jsonb_typeof(ask) = 'string';
        """,
    ),
    (
        81,
        "unwrap double-encoded JSONB in digest_deliveries and log_signatures (#382)",
        # Same bug as 80: json.dumps(...) handed to a pool whose JSONB codec
        # encodes again stored JSON *strings*. Unwrap in place; idempotent
        # (arrays/objects and NULLs are left alone).
        """
        UPDATE digest_deliveries SET payload = (payload #>> '{}')::jsonb
         WHERE jsonb_typeof(payload) = 'string';
        UPDATE log_signatures SET sample_lines = (sample_lines #>> '{}')::jsonb
         WHERE jsonb_typeof(sample_lines) = 'string';
        """,
    ),
    (
        82,
        "watcher_runs and known patterns are scoped to a stack, and the ticket is tracker-neutral",
        # A second watched stack (#378, #317) shares these tables. Three things
        # made that unsafe:
        #
        #  * `watcher_runs` had no repo at all, so the tick watermark, the
        #    unparseable back-off, the daily analysis budget, dedupe and the
        #    briefing were process-global — stack A would throttle and pollute
        #    stack B.
        #  * the filed ticket was `linear_issue_identifier`, a column only one
        #    tracker can fill. It becomes the same (tracker, key, url) triple
        #    the healing ledger already uses.
        #  * `watcher_known_patterns` keyed benign memory on the fingerprint
        #    alone though it carried a repo, so two stacks would overwrite each
        #    other's notes and read each other's.
        #
        # Existing rows keep repo '' — "the stack that ran before stacks
        # existed". The orchestrator adopts them for its configured watcher
        # repo at startup (WatcherRunsRepository.adopt_unscoped_rows), which
        # needs a value no migration can see.
        """
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS repo        TEXT NOT NULL DEFAULT '';
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS tracker     TEXT;
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS tracker_key TEXT;
        ALTER TABLE watcher_runs ADD COLUMN IF NOT EXISTS tracker_url TEXT;

        UPDATE watcher_runs
           SET tracker = 'linear', tracker_key = linear_issue_identifier
         WHERE linear_issue_identifier IS NOT NULL AND tracker_key IS NULL;

        ALTER TABLE watcher_runs DROP COLUMN IF EXISTS linear_issue_identifier;

        CREATE INDEX IF NOT EXISTS idx_watcher_runs_repo_created
            ON watcher_runs (repo, created_at DESC);

        -- Benign-pattern memory is per (fingerprint, repo), like log_signatures.
        UPDATE watcher_known_patterns SET repo = '' WHERE repo IS NULL;
        ALTER TABLE watcher_known_patterns ALTER COLUMN repo SET DEFAULT '';
        ALTER TABLE watcher_known_patterns ALTER COLUMN repo SET NOT NULL;
        ALTER TABLE watcher_known_patterns DROP CONSTRAINT IF EXISTS watcher_known_patterns_pkey;
        ALTER TABLE watcher_known_patterns ADD PRIMARY KEY (fingerprint, repo);

        -- The tick mark was a single row by construction (CHECK (id = 1)):
        -- two stacks would have fought over one anchor, so each restart could
        -- read the other stack's tick as its own and skip a scheduled one.
        ALTER TABLE watcher_tick_attempts DROP CONSTRAINT IF EXISTS watcher_tick_attempts_id_check;
        ALTER TABLE watcher_tick_attempts ADD COLUMN IF NOT EXISTS repo TEXT NOT NULL DEFAULT '';
        ALTER TABLE watcher_tick_attempts DROP CONSTRAINT IF EXISTS watcher_tick_attempts_pkey;
        ALTER TABLE watcher_tick_attempts DROP COLUMN IF EXISTS id;
        ALTER TABLE watcher_tick_attempts ADD PRIMARY KEY (repo);

        COMMENT ON TABLE watcher_tick_attempts IS
            '#335/#378: one row per watched stack — when that stack''s watcher loop last STARTED a tick.';

        -- One open case per ticket, per repo: GitHub/GitLab issue numbers are
        -- unique only within their repo, which is why github was kept out of
        -- case-opening (healing/recorder.py) while this index was global.
        DROP INDEX IF EXISTS idx_healing_cases_open_tracker;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_cases_open_tracker
            ON healing_cases (repo, tracker, tracker_key)
            WHERE tracker IS NOT NULL AND stage <> 'closed';
        """,
    ),
    (
        83,
        "watcher_stacks — what embry0 watches, one row per stack (#378, #297)",
        # The watcher read 24 flat WATCHER_* env vars, so "what do we watch"
        # was one value per process and changing it meant a redeploy (which
        # kills running jobs). A stack is now a row: its LogQL, where its
        # findings are filed, and the settings that differ from the global
        # defaults.
        #
        # `repo` is the primary key AND the scope key every shared table
        # already uses (watcher_runs.repo, log_signatures.repo,
        # healing_cases.repo), so a stack cannot exist twice under two names.
        #
        # `settings` holds only OVERRIDES. An absent key inherits the env
        # default, so a deployment that never writes a row behaves exactly as
        # it did — and a stack configured through the WATCHER_* env vars is
        # seeded from them at startup, once, only when the table is empty.
        """
        CREATE TABLE IF NOT EXISTS watcher_stacks (
            repo           TEXT PRIMARY KEY,
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            logql          TEXT NOT NULL,
            tracker        TEXT NOT NULL DEFAULT 'linear'
                CHECK (tracker IN ('linear', 'github', 'gitlab')),
            tracker_target JSONB NOT NULL DEFAULT '{}'::jsonb,
            settings       JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        COMMENT ON COLUMN watcher_stacks.tracker_target IS
            'Where findings are filed: {"team_id", "project_id"} for linear; {} for github/gitlab, which file into the stack repo itself.';
        COMMENT ON COLUMN watcher_stacks.settings IS
            'Overrides only; an absent key inherits the global WATCHER_* default (services/watcher_stacks.py).';
        """,
    ),
    (
        84,
        "healing_cases.resolution_path — allow 'abandoned' (#410)",
        # A case accepted, attempted and given up had no honest resolution:
        # the five existing paths all claim something that did not happen
        # (cancelled = never accepted, self_resolved = it stopped by itself).
        # Without this value the ledger could not close such a case at all,
        # so it sat at `accepted` forever and the dead-man alert for an
        # undispatched acceptance paged on it — masking real ones behind it.
        #
        # The whole predicate is replaced rather than extended: it is a CHECK
        # over TEXT, not an enum. DROP IF EXISTS keeps a re-run idempotent.
        """
        ALTER TABLE healing_cases
            DROP CONSTRAINT IF EXISTS healing_cases_resolution_path_check;
        ALTER TABLE healing_cases
            ADD CONSTRAINT healing_cases_resolution_path_check
            CHECK (resolution_path IN ('embry0', 'out_of_band', 'cancelled',
                                       'non_code', 'self_resolved', 'abandoned'));
        """,
    ),
    (
        85,
        "backfill watcher_runs.released_at for drafts a human already accepted (#415)",
        # Acceptance moved the case off `tracked`, which frees the ledger cap,
        # but nothing stamped released_at. The legacy predicate still counted
        # the row, and a reader of released_at could not tell an answered
        # draft from one awaiting a human. Stamp the slot from accepted_at.
        # A draft still at `tracked` has no accepted_at and keeps its slot.
        # Already-released rows are left alone, so a re-run changes nothing.
        """
        UPDATE watcher_runs w
           SET released_at = src.accepted_at
          FROM (
              SELECT watcher_run_id, MIN(accepted_at) AS accepted_at
                FROM healing_cases
               WHERE accepted_at IS NOT NULL AND watcher_run_id IS NOT NULL
               GROUP BY watcher_run_id
          ) src
         WHERE w.id = src.watcher_run_id
           AND w.released_at IS NULL;

        COMMENT ON COLUMN watcher_runs.released_at IS
            '#278/#415: when this draft stopped holding a cap slot — reconcile (cancelled/done/missing) or acceptance. The ticket id is kept.';
        """,
    ),
    (
        86,
        "credential registry — credentials, credential_bindings, credential_uses (spec 2026-09-29)",
        # One row per account embry0 acts as (a forge token, an LLM OAuth
        # token or API key, an SSH key), encrypted with the same Fernet
        # provider as repo_environment. Bindings say which account a repo,
        # owner, host or the whole deployment uses for a role. Uses is the
        # per-job ledger: which account did job X run as.
        """
        CREATE TABLE IF NOT EXISTS credentials (
            id                TEXT PRIMARY KEY,
            kind              TEXT NOT NULL CHECK (kind IN ('forge_token','llm_oauth','llm_api_key','ssh_key')),
            provider          TEXT NOT NULL CHECK (provider IN ('github','gitlab','anthropic','xai','ssh')),
            host              TEXT NOT NULL,
            label             TEXT NOT NULL,
            git_author_name   TEXT,
            git_author_email  TEXT,
            secret_ciphertext TEXT NOT NULL,
            public_material   TEXT,
            masked_preview    TEXT NOT NULL,
            verified_login    TEXT,
            status            TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','disabled','quarantined')),
            status_reason     TEXT,
            quarantined_until TIMESTAMPTZ,
            created_by        TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rotated_at        TIMESTAMPTZ,
            last_used_at      TIMESTAMPTZ,
            UNIQUE (provider, label)
        );

        CREATE TABLE IF NOT EXISTS credential_bindings (
            id            BIGSERIAL PRIMARY KEY,
            scope_kind    TEXT NOT NULL CHECK (scope_kind IN ('repo','owner','host','global')),
            scope_value   TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('forge','llm','deploy')),
            provider      TEXT NOT NULL,
            credential_id TEXT NOT NULL REFERENCES credentials(id) ON DELETE RESTRICT,
            pinned        BOOLEAN NOT NULL DEFAULT FALSE,
            priority      INTEGER NOT NULL DEFAULT 0,
            created_by    TEXT NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (scope_kind, scope_value, role, credential_id)
        );
        -- One credential per scope, role and provider everywhere except the
        -- global LLM pool, which is many by design.
        CREATE UNIQUE INDEX IF NOT EXISTS credential_bindings_single
            ON credential_bindings (scope_kind, scope_value, role, provider)
            WHERE scope_kind <> 'global' OR role <> 'llm';

        CREATE TABLE IF NOT EXISTS credential_uses (
            id               BIGSERIAL PRIMARY KEY,
            credential_id    TEXT REFERENCES credentials(id) ON DELETE SET NULL,
            credential_label TEXT NOT NULL,
            job_id           TEXT NOT NULL,
            role             TEXT NOT NULL,
            provider         TEXT NOT NULL,
            resolved_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            outcome          TEXT
        );
        CREATE INDEX IF NOT EXISTS credential_uses_job ON credential_uses (job_id);
        CREATE INDEX IF NOT EXISTS credential_uses_cred ON credential_uses (credential_id, resolved_at DESC);
        """,
    ),
    (
        87,
        "repos.docker_access — the shared Docker daemon is an admin grant per repo (#522)",
        # A profile with dind_enabled used to hand every sandbox on it the
        # client certs of the privileged daemon that also runs the credential
        # proxies and every other sandbox — the builtin qa-jvm included. From
        # here on the profile only asks; the repo row has to say yes.
        #
        # Backfill: a repo that already selects a dind_enabled profile keeps
        # the daemon, so the upgrade changes nothing for it. "Selects" means
        # any place a profile name is stored for the repo: its preference,
        # its stored qa.yaml (every `sandbox_profile:` value, quoted or not,
        # in any layer), or a non-QA job that ran on such a profile. QA job
        # rows are left out on purpose: standalone QA stamps `qa-jvm` on every
        # row as a label, whatever profile the run resolved. A yaml comment
        # of the form `# sandbox_profile: x` also counts — the backfill errs
        # towards keeping access a repo may rely on, never towards granting
        # it to a repo that did not have it.
        """
        ALTER TABLE repos ADD COLUMN IF NOT EXISTS docker_access BOOLEAN NOT NULL DEFAULT FALSE;

        COMMENT ON COLUMN repos.docker_access IS
            '#522: admin grant. When true, a sandbox for this repo whose profile sets dind_enabled gets the shared Docker daemon (client certs + DOCKER_HOST): code from the repo can then control that daemon, including the credential proxies holding forge tokens. No profile, qa.yaml or job can set it.';

        UPDATE repos r
           SET docker_access = TRUE
         WHERE NOT r.docker_access
           AND r.repo IN (
               SELECT p.repo
                 FROM repo_preferences p
                 JOIN sandbox_profiles s ON s.name = p.sandbox_profile AND s.dind_enabled
               UNION
               SELECT c.repo
                 FROM repo_qa_configs c
                 CROSS JOIN LATERAL regexp_matches(
                     c.yaml_text,
                     'sandbox_profile["'']?[[:blank:]]*:[[:blank:]]*["'']?([^"''[:space:],}#]+)',
                     'g'
                 ) AS m(v)
                 JOIN sandbox_profiles s ON s.name = m.v[1] AND s.dind_enabled
               UNION
               SELECT j.repo
                 FROM jobs j
                 JOIN sandbox_profiles s ON s.name = j.sandbox_profile AND s.dind_enabled
                WHERE j.pipeline_template IS DISTINCT FROM 'qa'
           );
        """,
    ),
    (
        88,
        "api_keys.allowed_repos — a per-key repository allowlist",
        # NULL means the key may start work on any repository, which is what
        # every key minted before this column could do, so nothing changes on
        # upgrade. An empty array would read as "no repository" to one reader
        # and "unrestricted" to another, so it is refused: the API stores NULL
        # when an operator clears the list.
        """
        ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS allowed_repos TEXT[];

        ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS api_keys_allowed_repos_not_empty;
        ALTER TABLE api_keys ADD CONSTRAINT api_keys_allowed_repos_not_empty
            CHECK (allowed_repos IS NULL OR cardinality(allowed_repos) > 0);

        COMMENT ON COLUMN api_keys.allowed_repos IS
            'Repositories (owner/name) this key may start or continue work on. NULL: unrestricted. Matched case-insensitively and through REPO_RENAMES.';
        """,
    ),
    (
        89,
        "jobs.finished_at backfill for terminal rows written without one (#345)",
        # Until #345, fail_orphaned (boot-time reconciliation) and the
        # paused-job expiry fallback set a terminal status in raw SQL without
        # finished_at, bypassing the invariant update() keeps (RAV-1271). Those
        # rows have no duration, so the job-duration histogram and every "how
        # long did it take" query silently left them out.
        #
        # The backfilled value is updated_at: the trigger stamped it when the
        # terminal write happened, and it is the latest time the row is known
        # to have been touched — never earlier than the real end. Idempotent:
        # a stamped row no longer matches. jobs.updated_at is preserved the
        # way migration 71 does it, for the same reason (see the comment
        # there).
        """
        ALTER TABLE jobs DISABLE TRIGGER trg_jobs_updated_at;
        UPDATE jobs
           SET finished_at = COALESCE(updated_at, created_at)
         WHERE finished_at IS NULL
           AND status IN ('completed', 'partial', 'failed', 'cancelled', 'expired', 'pr_merged', 'pr_closed');
        ALTER TABLE jobs ENABLE TRIGGER trg_jobs_updated_at;
        """,
    ),
    (
        90,
        "qa_volume_state — drop the rows of per-job volumes whose job has ended (#328)",
        # Nothing deleted these rows until #328, so every per-job volume ever
        # created left one behind. A per-job volume is removed once its job is
        # terminal (destroy_for_job, or the VolumeReaper backstop), so a row
        # whose job is terminal — or has no jobs row at all — describes a
        # volume that is gone or about to be. Losing a row only costs a
        # re-warm; per-repo rows are shared and left alone.
        """
        DELETE FROM qa_volume_state s
         WHERE s.scope = 'per-job'
           AND NOT EXISTS (
               SELECT 1 FROM jobs j
                WHERE j.job_id = s.scope_key
                  AND j.status NOT IN (
                      'completed', 'partial', 'failed', 'cancelled', 'expired', 'skipped', 'pr_merged', 'pr_closed'
                  )
           );
        """,
    ),
    (
        91,
        "pipeline_templates.agent_models — drop the map nothing read (#468)",
        # Stored by the template API since the table existed and read by no
        # execution path: a template names a model on each step's
        # config.model, which the resolver does read. The API now refuses the
        # field, so the column goes with it.
        """
        ALTER TABLE pipeline_templates DROP COLUMN IF EXISTS agent_models;
        """,
    ),
    (
        92,
        "jobs.interrupted_at / resume_attempts / launch — a restart recovers jobs instead of failing them (#307)",
        """
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS interrupted_at TIMESTAMPTZ;
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_attempts INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE jobs ADD COLUMN IF NOT EXISTS launch JSONB;

        COMMENT ON COLUMN jobs.interrupted_at IS
            '#307: when an orchestrator shutdown (drain) last stopped or deferred this job. Cleared when boot recovers the job; left set on a job boot could not recover.';
        COMMENT ON COLUMN jobs.resume_attempts IS
            '#307: boot recoveries after an unclean stop (a crash or kill, no drain). Bounded by MAX_RESUME_ATTEMPTS so a job that takes the orchestrator down cannot loop.';
        COMMENT ON COLUMN jobs.launch IS
            '#307: what the executor needs to start this job again from the beginning (runner kind and its arguments). NULL on rows written before it.';
        """,
    ),
    (
        93,
        "qa_app_results.trace_url — drop the column nothing ever set (#644)",
        # No QA node wrote a trace URL, so every row is NULL; the API and the
        # dashboard dropped the field in #642 and the code in this change.
        # QA traces are served by the authenticated per-app artifact route.
        """
        ALTER TABLE qa_app_results DROP COLUMN IF EXISTS trace_url;
        """,
    ),
    (
        94,
        "healing_cases.signature_source — how the watcher chose a case's signature (#657)",
        # #290 anchored every watcher case to the window's loudest cluster,
        # whatever the proposal was about; #657 anchors it to a cluster the
        # proposal's own evidence quotes and falls back to the loudest only
        # when nothing does. Confirm-healed must tell the two apart, because a
        # fallback signature says nothing about whether this case's fix
        # worked. NULL: not a watcher anchor, or opened before this column.
        # The vocabulary is SignatureSource in services/healing/lifecycle.py.
        """
        ALTER TABLE healing_cases ADD COLUMN IF NOT EXISTS signature_source TEXT
            CHECK (signature_source IN ('evidence', 'fallback'));

        COMMENT ON COLUMN healing_cases.signature_source IS
            '#657: how the watcher chose signature_id. evidence = a window cluster the proposal quotes; fallback = nothing it quotes is in the window, so the window''s loudest line (confirm-healed answers inconclusive). NULL = not a watcher anchor, or opened before #657.';
        """,
    ),
    (
        95,
        "job_logs correlation columns — run_id, node, event_type; traces.result admits running (#36)",
        # Every event already lands in job_logs as JSON text. These columns
        # are the same fields lifted out so a run, a node or a type can be
        # indexed without scanning the JSON; agent_id was never written. A
        # trace row is now written at run start as 'running' and finalized
        # at the end, so the column's vocabulary gains that value (no CHECK
        # existed; this documents it).
        """
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS run_id TEXT;
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS node TEXT;
        ALTER TABLE job_logs ADD COLUMN IF NOT EXISTS event_type TEXT;
        ALTER TABLE job_logs DROP COLUMN IF EXISTS agent_id;
        CREATE INDEX IF NOT EXISTS idx_job_logs_job_event_type ON job_logs (job_id, event_type);
        CREATE INDEX IF NOT EXISTS idx_job_logs_run_id ON job_logs (run_id) WHERE run_id IS NOT NULL;

        COMMENT ON COLUMN traces.result IS
            '#36: success | error | running (written at run start, replaced at run end)';
        """,
    ),
    (
        96,
        "pr_reviews + repo_preferences.pr_review — review a pull request when it is ready (#30)",
        # One row per review a trigger asked for. trigger_key is the dedupe
        # key — ready:<repo>#<number>@<sha> for a ready event,
        # command:<forge>:<comment id> for a /embry0 review comment — and the
        # unique index makes a redelivered webhook a no-op across restarts.
        # job_id is SET NULL when a job row is purged, so the earlier review's
        # verdict outlives it and the next review can still read it.
        """
        ALTER TABLE repo_preferences ADD COLUMN IF NOT EXISTS pr_review JSONB;
        COMMENT ON COLUMN repo_preferences.pr_review IS
            '#30: pull request review settings {"enabled": bool, "depth": "full"|"targeted"|"read_only", "model": str|null, "ready_authors": "team"|"anyone"}. NULL = never configured, which means off; a missing ready_authors means team.';

        CREATE TABLE IF NOT EXISTS pr_reviews (
            id          TEXT PRIMARY KEY,
            repo        TEXT NOT NULL,
            number      INTEGER NOT NULL,
            head_sha    TEXT NOT NULL,
            trigger     TEXT NOT NULL CHECK (trigger IN ('ready', 'command')),
            trigger_key TEXT NOT NULL,
            job_id      TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
            verdict     JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            posted_at   TIMESTAMPTZ
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pr_reviews_trigger_key ON pr_reviews (trigger_key);
        CREATE INDEX IF NOT EXISTS idx_pr_reviews_repo_number ON pr_reviews (repo, number, created_at DESC);
        COMMENT ON COLUMN pr_reviews.verdict IS
            '#30: the reviewer''s parsed verdict, kept whether or not it was posted. The next review of the same pull request reads it.';
        COMMENT ON COLUMN pr_reviews.posted_at IS
            '#30: when the review landed on the forge. NULL = not (yet) posted.';
        """,
    ),
    (
        97,
        "organizations — the forge owners embry0 maintains as a whole (ADR 0010)",
        # Every sense embry0 has (webhook or tracker intake, log watching, QA)
        # is switched on per repository, so an owner with dozens of
        # repositories is dozens of decisions nobody takes. The organization
        # is the row that makes the owner the unit: its forge, the credential
        # embry0 acts with, and what that credential can do there (`access`,
        # written by the probe). Discovery, the needs list and the wizard
        # hang from it.
        #
        # `owner` is stored lowercase, the way the forges compare it. The
        # credential is a soft reference: deleting the credential leaves the
        # organization, with no way to act, which the needs list reports.
        """
        CREATE TABLE IF NOT EXISTS organizations (
            owner          TEXT PRIMARY KEY,
            forge          TEXT NOT NULL CHECK (forge IN ('github', 'gitlab')),
            credential_id  TEXT REFERENCES credentials(id) ON DELETE SET NULL,
            display_name   TEXT,
            settings       JSONB NOT NULL DEFAULT '{}'::jsonb,
            access         JSONB NOT NULL DEFAULT '{}'::jsonb,
            repo_count     INTEGER,
            discovered_at  TIMESTAMPTZ,
            status         TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'paused', 'removed')),
            created_by     TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        COMMENT ON TABLE organizations IS
            'ADR 0010: a forge owner embry0 maintains as a whole. access = what the credential can do there, from the probe; settings = defaults for repositories under it.';
        """,
    ),
]


async def run_migrations(db: DatabasePool) -> None:
    """Run all pending migrations, serialised across processes.

    Concurrent callers queue on a session-level advisory lock; see this
    module's docstring for why the lock is session-scoped rather than
    transaction-scoped.
    """
    if db.pool is None:
        raise RuntimeError("Call db.connect() first")

    async with db.pool.acquire() as conn:
        # Lock first — before the ledger table exists. Two concurrent
        # CREATE TABLE IF NOT EXISTS statements race inside pg_type and one
        # of them raises a duplicate-key error, so even that is not safe to
        # run unserialised.
        #
        # The acquisition runs as a task so the threshold can be logged while
        # it is still blocked: a post-hoc duration would only surface once the
        # stall was over, which is exactly when nobody needs it any more.
        started = time.monotonic()
        acquisition = asyncio.ensure_future(conn.execute("SELECT pg_advisory_lock($1)", MIGRATIONS_ADVISORY_LOCK_KEY))
        done, _ = await asyncio.wait({acquisition}, timeout=LOCK_WAIT_LOG_THRESHOLD_SECONDS)
        if not done:
            logger.info("migrations_lock_waiting", lock_key=MIGRATIONS_ADVISORY_LOCK_KEY)
        await acquisition
        waited_seconds = time.monotonic() - started
        logger.info(
            "migrations_lock_acquired",
            lock_key=MIGRATIONS_ADVISORY_LOCK_KEY,
            waited_seconds=round(waited_seconds, 3),
        )

        try:
            # Create migrations tracking table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS embry0_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # Read the applied set under the lock, so a runner that queued
            # behind another one sees the winner's work and skips it.
            #
            # Track by set membership, not just the max applied version — a
            # branch-merge can land a migration numbered below the
            # already-applied max, and comparing against MAX() alone would
            # silently skip it forever with no error (see migration 33's
            # renumber-to-top fix).
            applied = {row["version"] for row in await conn.fetch("SELECT version FROM embry0_migrations")}

            for version, description, sql in MIGRATIONS:
                if version in applied:
                    continue
                logger.info("migration_applying", version=version, description=description)
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO embry0_migrations (version, description) VALUES ($1, $2)",
                        version,
                        description,
                    )
                logger.info("migration_applied", version=version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", MIGRATIONS_ADVISORY_LOCK_KEY)

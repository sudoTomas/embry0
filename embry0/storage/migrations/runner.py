"""Simple forward-only migration runner for PostgreSQL."""

import structlog

from embry0.storage.database import DatabasePool

logger = structlog.get_logger(__name__)

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
]


async def run_migrations(db: DatabasePool) -> None:
    """Run all pending migrations."""
    if db.pool is None:
        raise RuntimeError("Call db.connect() first")

    async with db.pool.acquire() as conn:
        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS embry0_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Get current version
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM embry0_migrations")

        for version, description, sql in MIGRATIONS:
            if version <= current:
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

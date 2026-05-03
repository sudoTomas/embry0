"""Simple forward-only migration runner for PostgreSQL."""

import structlog

from athanor.storage.database import DatabasePool

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
            base_image      TEXT NOT NULL DEFAULT 'athanor-sandbox:latest',
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
            trigger_labels      JSONB DEFAULT '["Athanor"]',
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
          AND trigger_labels = '["Athanor"]'::jsonb
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
        # ShareLock briefly but is correct on Athanor's dataset sizes.
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
        # is_builtin: marks profiles seeded by Athanor; UI hides destructive ops on these
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
]


async def run_migrations(db: DatabasePool) -> None:
    """Run all pending migrations."""
    if db.pool is None:
        raise RuntimeError("Call db.connect() first")

    async with db.pool.acquire() as conn:
        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS athanor_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Get current version
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM athanor_migrations")

        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            logger.info("migration_applying", version=version, description=description)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO athanor_migrations (version, description) VALUES ($1, $2)",
                    version,
                    description,
                )
            logger.info("migration_applied", version=version)

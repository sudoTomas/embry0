"""Simple forward-only migration runner for PostgreSQL."""

import structlog

from legion.storage.database import DatabasePool

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
            base_image      TEXT NOT NULL DEFAULT 'legion-sandbox:latest',
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
            trigger_labels      JSONB DEFAULT '["Legion"]',
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
]


async def run_migrations(db: DatabasePool) -> None:
    """Run all pending migrations."""
    if db.pool is None:
        raise RuntimeError("Call db.connect() first")

    async with db.pool.acquire() as conn:
        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS legion_migrations (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # Get current version
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM legion_migrations")

        for version, description, sql in MIGRATIONS:
            if version <= current:
                continue
            logger.info("migration_applying", version=version, description=description)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO legion_migrations (version, description) VALUES ($1, $2)",
                    version,
                    description,
                )
            logger.info("migration_applied", version=version)

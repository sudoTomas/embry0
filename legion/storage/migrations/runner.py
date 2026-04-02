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

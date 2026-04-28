"""Application configuration via Pydantic BaseSettings."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AthanorConfig(BaseSettings):
    """All runtime configuration for the Athanor orchestrator."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database (PostgreSQL)
    database_url: str = "postgresql://athanor:athanor@localhost:5432/athanor"

    # GitHub integration
    github_token: str = ""
    github_webhook_secret: str = ""

    # API authentication
    api_key: str = ""
    dev_mode: bool = False
    allowed_cors_origins: str = ""
    trigger_labels: str = "Athanor"

    # Rate limiting
    rate_limit_per_author_per_hour: int = 5
    api_rate_limit_per_minute: int = 60

    # Agent provider (anthropic_api | claude_max | ollama)
    provider_mode: str = "anthropic_api"
    anthropic_api_key: str = ""
    claude_max_oauth_token: str = ""
    ollama_base_url: str = ""
    ollama_model: str = ""
    default_model: str = ""
    model_heavy: str = "claude-opus-4-7"
    model_medium: str = "claude-sonnet-4-6"
    model_light: str = "claude-haiku-4-5"

    # Budget
    max_budget_usd: float = 10.0
    daily_budget_cap_usd: float = 100.0
    monthly_budget_cap_usd: float = 500.0
    budget_overrun_mode: str = "soft"  # "soft" | "hard"

    # Sandbox defaults
    sandbox_image: str = "athanor-sandbox:latest"
    sandbox_memory: str = "8g"
    sandbox_cpus: str = "4"

    # Paused job TTL
    paused_job_ttl_hours: int = 48

    # Queue
    max_global_concurrent_jobs: int = 10

    # Audit
    audit_log_path: Path | None = None

    # Notifications
    slack_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""

    # Environment variable encryption
    environment_secret_key: str = ""

    # Shared secret between orchestrator and credential proxies. The orchestrator
    # passes this as PROXY_ADMIN_TOKEN env var into each proxy container; admin
    # endpoints (/admin/enroll, etc.) require it via X-Admin-Token. Required in
    # production. If empty in dev_mode, the orchestrator generates a random
    # value at startup with a warning. Required (refuses to start) otherwise.
    proxy_admin_token: str = ""

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

    # Pluggable agent execution modes (Phase 1).
    # Both dimensions are orthogonal. Defaults preserve today's runtime
    # behavior (SDK path + OAuth from ~/.claude/.credentials.json).
    default_execution_mode: str = "sdk"
    default_auth_mode: str = "oauth"

    @field_validator("audit_log_path", mode="before")
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

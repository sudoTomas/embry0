"""Application configuration via Pydantic BaseSettings."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Embry0Config(BaseSettings):
    """All runtime configuration for the embry0 orchestrator."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database (PostgreSQL)
    database_url: str = "postgresql://embry0:embry0@localhost:5432/embry0"

    # GitHub integration
    github_token: str = ""
    github_webhook_secret: str = ""

    # Linear integration (EMB-47). All empty = integration off (the webhook
    # route answers "not configured"). linear_repo_map is a JSON object mapping
    # Linear project name (or team key) → GitHub owner/repo the pipeline runs
    # against, e.g. {"Raven AI Quoting Platform": "raven-cargo/ai-quoting"}.
    linear_api_key: str = ""
    linear_webhook_secret: str = ""
    linear_repo_map: str = ""

    # API authentication
    api_key: str = ""
    # Per-surface dev-mode flags. Each independently bypasses the corresponding
    # auth check. Both default to False (production posture).
    auth_dev_mode: bool = False
    webhook_dev_mode: bool = False
    allowed_cors_origins: str = ""
    trigger_labels: str = "embry0"

    # Rate limiting — defaults sized for trusted self-hosted operation
    # (RAV-605); tighten via env for anything internet-facing.
    rate_limit_per_author_per_hour: int = 20
    api_rate_limit_per_minute: int = 300

    # Webhook request-body ceiling (bytes). Large monorepo push payloads can
    # exceed 1 MiB; 5 MiB keeps DoS exposure bounded on a LAN-only API.
    max_webhook_body_bytes: int = Field(default=5_242_880, gt=0)

    # Agent provider (anthropic_api | claude_max | ollama)
    provider_mode: str = "anthropic_api"
    anthropic_api_key: str = ""
    # Renamed from CLAUDE_MAX_OAUTH_TOKEN to CLAUDE_CODE_OAUTH_TOKEN (2026-04-28, Plan D)
    # to match auth_provider.py, sandbox, and CLAUDE.md documentation.
    claude_code_oauth_token: str = ""
    ollama_base_url: str = ""
    ollama_model: str = ""
    default_model: str = ""
    model_heavy: str = "claude-opus-4-7"
    model_medium: str = "claude-sonnet-4-6"
    model_light: str = "claude-haiku-4-5"

    # Budget
    max_budget_usd: float = 20.0
    daily_budget_cap_usd: float = 100.0
    monthly_budget_cap_usd: float = 500.0
    budget_overrun_mode: str = "soft"  # "soft" | "hard"

    # Sandbox defaults
    sandbox_image: str = "embry0-sandbox:latest"
    sandbox_memory: str = "8g"
    sandbox_cpus: str = "4"

    # Container image registry prefix applied to embry0-* images at DinD-launch
    # time (see embry0.execution.image_registry.qualify_image). Empty disables
    # qualification — useful for tests and for environments that load images
    # directly into the daemon. The bootstrapped compose stack runs a sidecar
    # registry on the backend network; on K8s migration this becomes the
    # production registry URL with no code changes.
    image_registry: str = ""

    # Paused job TTL
    paused_job_ttl_hours: int = 48

    # Audit
    audit_log_path: Path | None = None

    # Notifications
    slack_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_url: str = ""

    # Public-facing base URL for the dashboard. Used in cross-channel
    # notifications (e.g. GitHub-comment ask-user) to construct deep links the
    # user can click to answer pending questions. Defaults to the local
    # nginx-served dashboard port.
    dashboard_public_url: str = "http://localhost:8200"

    # Environment variable encryption
    environment_secret_key: str = ""

    # Shared secret between orchestrator and credential proxies. The orchestrator
    # passes this as PROXY_ADMIN_TOKEN env var into each proxy container; admin
    # endpoints (/admin/enroll, etc.) require it via X-Admin-Token. Required in
    # production. If empty in auth_dev_mode, the orchestrator generates a random
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

    # xAI direct-API OAuth path (EMB-45). When enabled, the orchestrator launches
    # the xai-proxy sidecar and owns the rotating SuperGrok refresh token. The
    # durable, Fernet-encrypted credential store is seeded once from the Grok CLI
    # store (``xai_grok_cli_store``, default ~/.grok/auth.json) and then owned by
    # embry0. Requires ENVIRONMENT_SECRET_KEY. Defaults False (opt-in) — leaving it
    # off keeps grok on the EMB-36 CLI/console-key path.
    xai_proxy_enabled: bool = False
    xai_credential_path: str = "/data/embry0/xai_credential.enc"
    xai_grok_cli_store: str = ""

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
    minio_root_password: str = ""
    qa_artifact_retention_days: int = Field(default=14, gt=0, le=3650)

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

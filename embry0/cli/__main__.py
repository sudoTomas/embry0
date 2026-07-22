"""embry0 CLI — manage the containerized production stack."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────────────────

COMPOSE_FILE = "infra/docker-compose.yml"
ENV_FILE = ".env"
PROJECT_NAME = "embry0"
DIND_CONTAINER = "embry0-dind"
SANDBOX_IMAGE = "embry0-sandbox:latest"

_SECRET_FIELDS = frozenset(
    {
        "github_token",
        "github_webhook_secret",
        "api_key",
        "anthropic_api_key",
        "claude_code_oauth_token",
        "telegram_bot_token",
        "slack_webhook_url",
    }
)

# ── Colors ───────────────────────────────────────────────────────────────────

if sys.stderr.isatty():
    _GREEN = "\033[0;32m"
    _RED = "\033[0;31m"
    _YELLOW = "\033[0;33m"
    _BOLD = "\033[1m"
    _NC = "\033[0m"
else:
    _GREEN = _RED = _YELLOW = _BOLD = _NC = ""


def _info(msg: str) -> None:
    print(f"{_GREEN}✓{_NC} {msg}")


def _warn(msg: str) -> None:
    print(f"{_YELLOW}⚠{_NC} {msg}")


def _err(msg: str) -> None:
    print(f"{_RED}✗{_NC} {msg}", file=sys.stderr)


def _header(msg: str) -> None:
    print(f"\n{_BOLD}{msg}{_NC}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mask_secret(value: str) -> str:
    """Mask a secret value for display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def _resolve_home() -> Path:
    """Resolve the project root from EMBRY0_HOME or cwd."""
    home = Path(os.environ.get("EMBRY0_HOME", ".")).resolve()
    if not (home / COMPOSE_FILE).exists():
        _err(f"{COMPOSE_FILE} not found in {home}")
        _err("Set EMBRY0_HOME to the project root, e.g.:")
        _err("  export EMBRY0_HOME=/opt/embry0")
        sys.exit(1)
    return home


def _compose_cmd(home: Path) -> list[str]:
    """Build the base docker compose command with project name and env file."""
    cmd = ["docker", "compose", "-f", str(home / COMPOSE_FILE), "-p", PROJECT_NAME]
    env_path = home / ENV_FILE
    if env_path.exists():
        cmd.extend(["--env-file", str(env_path)])
    return cmd


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command."""
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


# ── State checks ────────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """Check if the Docker daemon is running."""
    result = _run(["docker", "info"], check=False, capture=True)
    return result.returncode == 0


def _stack_running(home: Path) -> bool:
    """Check if all production services are running."""
    result = _run(
        _compose_cmd(home) + ["ps", "--format", "{{.Name}}", "--filter", "status=running"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        return False
    running = set(result.stdout.strip().splitlines())
    required = {"embry0-frontend", "embry0-orchestrator", "embry0-dind", "embry0-postgres"}
    return required.issubset(running)


def _images_exist(home: Path) -> bool:
    """Check if production images are built."""
    result = _run(
        _compose_cmd(home) + ["images", "--format", "{{.Repository}}"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        return False
    images = result.stdout.strip()
    return "embry0-frontend" in images and "embry0-orchestrator" in images


def _wait_for_dind(timeout: int = 60) -> bool:
    """Poll DinD container until healthy or timeout."""
    _info("Waiting for DinD to be ready...")
    for _ in range(timeout):
        result = _run(
            ["docker", "exec", DIND_CONTAINER, "docker", "info"],
            check=False,
            capture=True,
        )
        if result.returncode == 0:
            _info("DinD is healthy")
            return True
        time.sleep(1)
    _err(f"DinD not ready after {timeout}s")
    return False


def _sandbox_exists_in_dind() -> bool:
    """Check if the sandbox image exists inside DinD."""
    result = _run(
        ["docker", "exec", DIND_CONTAINER, "docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        check=False,
        capture=True,
    )
    return result.returncode == 0 and SANDBOX_IMAGE in result.stdout


def _build_sandbox(home: Path) -> None:
    """Build the sandbox image inside DinD."""
    _info("Building sandbox image inside DinD...")
    # Copy the entire project context (for Dockerfile.sandbox)
    _run(["docker", "cp", f"{home}/.", f"{DIND_CONTAINER}:/build-context/"])
    _run(
        [
            "docker",
            "exec",
            DIND_CONTAINER,
            "docker",
            "build",
            "-t",
            SANDBOX_IMAGE,
            "-f",
            "/build-context/infra/Dockerfile.sandbox",
            "/build-context/",
        ]
    )
    _info("Sandbox image built")


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_config(args: argparse.Namespace) -> None:
    """Validate and display current configuration."""
    from embry0.config import Embry0Config

    try:
        config = Embry0Config()
    except Exception as exc:
        _err(f"Error loading configuration: {exc}")
        sys.exit(1)

    issues: list[str] = []

    for field_name in sorted(Embry0Config.model_fields):
        value = getattr(config, field_name)
        if field_name in _SECRET_FIELDS:
            display = _mask_secret(str(value))
        else:
            display = str(value)
        print(f"{field_name}={display}")

    if not config.github_token:
        issues.append("GITHUB_TOKEN is not set")
    if config.provider_mode == "anthropic_api" and not config.anthropic_api_key:
        issues.append("ANTHROPIC_API_KEY is not set (required for provider_mode=anthropic_api)")
    if config.provider_mode == "claude_max" and not config.claude_code_oauth_token:
        issues.append("CLAUDE_CODE_OAUTH_TOKEN is not set (required for provider_mode=claude_max)")
    if config.provider_mode == "ollama" and not config.ollama_model:
        issues.append("OLLAMA_MODEL is not set (required for provider_mode=ollama)")

    if issues:
        print(f"\n{len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("\nConfiguration valid.")
        sys.exit(0)


def cmd_health(args: argparse.Namespace) -> None:
    """Check the health of a running embry0 instance."""
    import httpx

    port = os.environ.get("PROD_PORT", "8200")
    url = args.url or os.environ.get("EMBRY0_URL", f"http://localhost:{port}")
    endpoint = f"{url.rstrip('/')}/api/v1/health/ready"

    try:
        resp = httpx.get(endpoint, timeout=5.0)
        try:
            data = resp.json()
        except ValueError:
            _err(f"Unexpected response from {endpoint} (HTTP {resp.status_code})")
            sys.exit(1)

        status = data.get("status", "unknown")
        print(f"Status: {status}")

        checks = data.get("checks", {})
        for check_name, check_value in checks.items():
            print(f"  {check_name}: {check_value}")

        sys.exit(0 if status == "ok" else 1)
    except httpx.ConnectError:
        _err(f"Could not connect to {endpoint}")
        sys.exit(1)
    except Exception as exc:
        _err(f"Error: {exc}")
        sys.exit(1)


def cmd_start(args: argparse.Namespace) -> None:
    """Start the full production stack."""
    home = _resolve_home()

    if not _docker_available():
        _err("Docker daemon is not running")
        sys.exit(1)

    if _stack_running(home):
        _info("embry0 stack is already running")
        _run(_compose_cmd(home) + ["ps"])
        sys.exit(0)

    if not (home / ENV_FILE).exists():
        _err(f".env not found in {home}")
        _err("Create one from the template:")
        _err(f"  cp {home / '.env.example'} {home / '.env'}")
        sys.exit(1)

    if not _images_exist(home):
        _header("Building production images...")
        _run(_compose_cmd(home) + ["build"])

    _header("Starting embry0 stack...")
    env = os.environ.copy()
    if args.port:
        env["PROD_PORT"] = str(args.port)
    if args.host:
        env["HOST"] = args.host
    subprocess.run(_compose_cmd(home) + ["up", "-d"], check=True, env=env)

    if not _wait_for_dind():
        _err("Stack started but DinD failed health check")
        sys.exit(1)

    if not _sandbox_exists_in_dind():
        _build_sandbox(home)

    _header("embry0 is running")
    _run(_compose_cmd(home) + ["ps"])


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the production stack."""
    home = _resolve_home()
    _header("Stopping embry0 stack...")
    _run(_compose_cmd(home) + ["down"])
    _info("Stack stopped")


def cmd_build(args: argparse.Namespace) -> None:
    """Build production images."""
    home = _resolve_home()
    cmd = _compose_cmd(home) + ["build"]
    if not args.cached:
        cmd.append("--no-cache")
    _header("Building production images..." + (" (cached)" if args.cached else " (clean)"))
    _run(cmd)
    _info("Build complete")


def cmd_build_sandbox(args: argparse.Namespace) -> None:
    """Build the sandbox image inside DinD."""
    home = _resolve_home()

    result = _run(
        ["docker", "exec", DIND_CONTAINER, "docker", "info"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        _err("DinD container is not running. Start the stack first: embry0 start")
        sys.exit(1)

    _build_sandbox(home)


def cmd_purge(args: argparse.Namespace) -> None:
    """Remove embry0 Docker artifacts."""
    home = _resolve_home()
    purge_all = not (args.containers or args.images or args.volumes or args.networks)

    _header("Purging embry0 Docker artifacts...")

    if purge_all or args.containers:
        _info("Removing containers...")
        _run(_compose_cmd(home) + ["down", "--remove-orphans"], check=False)

    if purge_all or args.images:
        _info("Removing images...")
        _run(
            _compose_cmd(home) + ["down", "--rmi", "all", "--remove-orphans"],
            check=False,
        )

    if purge_all or args.volumes:
        _info("Removing volumes...")
        _run(
            _compose_cmd(home) + ["down", "--volumes", "--remove-orphans"],
            check=False,
        )

    if purge_all or args.networks:
        _info("Removing networks...")
        project_filter = f"label=com.docker.compose.project={PROJECT_NAME}"
        result = _run(
            ["docker", "network", "ls", "--filter", project_filter, "--format", "{{.Name}}"],
            check=False,
            capture=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            for network in result.stdout.strip().splitlines():
                _run(["docker", "network", "rm", network], check=False)

    _info("Purge complete")


def cmd_migrate_qa_config(args: argparse.Namespace) -> None:
    """Handle `embry0 migrate-qa-config`."""
    from embry0.cli.migrate_qa_config import MigrationError, migrate_v1_to_v2

    try:
        out = migrate_v1_to_v2(args.qa_path, app_name=args.app_name, write=args.write)
    except MigrationError as exc:
        _err(str(exc))
        sys.exit(2)

    if args.dry_run:
        sys.stdout.write(out)
        return

    _info(f"migrated {args.qa_path} (v1 backed up to qa.v1.yaml.bak)")


def cmd_build_qa_image(args: argparse.Namespace) -> None:
    """Handle ``embry0 build-qa-image``.

    Builds a pre-baked QA sandbox image for *repo* at *branch* (idempotent:
    skips when the active tag's ``lockfile_sha`` already matches the current
    lockfile on the target branch, unless ``--force`` is given).

    Dependency wiring
    -----------------
    This is the first CLI command that requires a live database connection.
    Other ``cmd_*`` handlers shell out to docker-compose and therefore need no
    DB access.  Here we follow the canonical pattern from
    ``tests/integration/conftest.py`` (``setup_database`` fixture):

    1. Load ``DATABASE_URL`` from the environment (or the project ``.env``).
    2. Construct a ``DatabasePool``, call ``connect()``, run ``run_migrations``.
    3. Build repository objects (``QAImageTagsRepository``,
       ``SandboxProfilesRepository``).
    4. Construct a ``DockerClient`` and ``SandboxManager`` (requires Docker
       daemon access — run inside the running embry0 stack or on a host with
       Docker access).
    5. Construct a lightweight proxy-manager shim (or pass ``proxy_mgr=None``
       if git-proxy URL injection is not needed for this build).

    For Phase-2 the full sandbox / proxy-manager wiring is highly coupled to
    the running compose stack (``embry0-dind``, ``embry0-orchestrator``).
    Rather than re-implement that bootstrap outside the app server this handler
    raises ``NotImplementedError`` with a clear ops-side message, while keeping
    the testable core (``run_build_qa_image``) fully implemented and unit-tested.

    Operators needing this functionality before a full CLI wiring pass can
    trigger the equivalent API endpoint::

        POST /api/v1/qa/images/build  {"repo": "...", "branch": "...", "force": false}
    """
    outcome = run_build_qa_image_via_api(
        repo=args.repo,
        branch=args.branch,
        force=args.force,
        api_url=args.api_url,
        api_key=args.api_key,
    )
    _info(f"build-qa-image {args.repo}@{args.branch}: {outcome}")


def _resolve_api_key(explicit: str | None) -> str | None:
    """CLI API-key resolution: --api-key > EMBRY0_API_KEY > API_KEY env >
    the project root .env (the operator runs this next to the stack)."""
    if explicit:
        return explicit
    for var in ("EMBRY0_API_KEY", "API_KEY"):
        val = os.environ.get(var)
        if val:
            return val
    env_file = Path.cwd() / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def run_build_qa_image_via_api(
    *,
    repo: str,
    branch: str,
    force: bool,
    api_url: str | None = None,
    api_key: str | None = None,
) -> str:
    """POST /api/v1/qa/images/build on the running orchestrator.

    The build's dependencies (DinD daemon + certs, Postgres, git-proxy)
    only exist inside the running compose stack, so the orchestrator is
    the sole sane executor — the CLI is a thin, long-timeout client of
    the EMB-42 endpoint. Exits non-zero with a clear message when the
    stack is down or auth is missing.
    """
    import httpx

    base = (api_url or os.environ.get("EMBRY0_API_URL") or "http://localhost:8200").rstrip("/")
    key = _resolve_api_key(api_key)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        resp = httpx.post(
            f"{base}/api/v1/qa/images/build",
            json={"repo": repo, "branch": branch, "force": force},
            headers=headers,
            # A fresh build (clone + npm ci + turbo build + docker commit)
            # takes minutes; the skip path returns in seconds.
            timeout=httpx.Timeout(1800.0, connect=10.0),
        )
    except httpx.ConnectError:
        _err(f"cannot reach the orchestrator at {base} — is the stack running? (embry0 start)")
        sys.exit(2)

    if resp.status_code == 401:
        _err("orchestrator rejected the request (401) — set EMBRY0_API_KEY or pass --api-key")
        sys.exit(2)
    if resp.status_code >= 400:
        _err(f"build failed ({resp.status_code}): {resp.text[:500]}")
        sys.exit(2)

    return str(resp.json().get("status", "unknown"))


def cmd_onboard(args: argparse.Namespace) -> None:
    """Handle ``embry0 onboard`` (EMB-50).

    Thin API client: POST /api/v1/jobs {pipeline: "onboard"} on the running
    orchestrator (the analysis sandbox + smoke boots only exist inside the
    stack), then poll the job until it finishes and print the outcome.
    """
    import time as _time

    import httpx

    base = (args.api_url or os.environ.get("EMBRY0_API_URL") or "http://localhost:8200").rstrip("/")
    key = _resolve_api_key(args.api_key)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        resp = httpx.post(
            f"{base}/api/v1/jobs",
            json={
                "pipeline": "onboard",
                "repo": args.repo,
                "branch": args.branch,
                "skip_smoke": args.no_smoke,
            },
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    except httpx.ConnectError:
        _err(f"cannot reach the orchestrator at {base} — is the stack running? (embry0 start)")
        sys.exit(2)
    if resp.status_code == 401:
        _err("orchestrator rejected the request (401) — set EMBRY0_API_KEY or pass --api-key")
        sys.exit(2)
    if resp.status_code >= 400:
        _err(f"onboard job creation failed ({resp.status_code}): {resp.text[:500]}")
        sys.exit(2)

    job_id = resp.json().get("job_id")
    _info(f"onboard job {job_id} started for {args.repo}@{args.branch} — analyzing…")

    # Poll until terminal. Analysis + up-to-3 rounds + smoke boots can take
    # a while; cap at 1h wall-clock.
    deadline = _time.monotonic() + 3600
    status = "running"
    job: dict[str, Any] = {}
    while _time.monotonic() < deadline:
        _time.sleep(10)
        try:
            job = httpx.get(f"{base}/api/v1/jobs/{job_id}", headers=headers, timeout=30.0).json()
        except Exception:  # noqa: BLE001
            continue
        status = job.get("status", "unknown")
        if status in ("completed", "failed", "cancelled"):
            break

    if status == "completed":
        _info(
            f"onboard completed — qa.yaml for {args.repo} is active in the config store "
            f"(repo-configs/{args.repo.replace('/', '__')}/qa.yaml). "
            f"Inspect: GET {base}/api/v1/repos/{args.repo}/qa-config"
        )
    else:
        _err(f"onboard {status}: {(job.get('error_message') or 'no detail')[:1000]}")
        sys.exit(1)


# ── Argparse ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the embry0 CLI."""
    parser = argparse.ArgumentParser(
        prog="embry0",
        description="embry0 — manage the containerized production stack",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    start_parser = subparsers.add_parser("start", help="Start the production stack")
    start_parser.add_argument("--port", type=int, default=None, help="Port (default: PROD_PORT env or 8200)")
    start_parser.add_argument("--host", type=str, default=None, help="Host (default: HOST env or 0.0.0.0)")

    # stop
    subparsers.add_parser("stop", help="Stop the production stack")

    # build
    build_parser = subparsers.add_parser("build", help="Build production images (clean, no cache)")
    build_parser.add_argument("--cached", action="store_true", help="Use Docker cache instead of clean build")

    # build-sandbox
    subparsers.add_parser("build-sandbox", help="Build sandbox image inside DinD")

    # health
    health_parser = subparsers.add_parser("health", help="Check health of running stack")
    health_parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Base URL (default: EMBRY0_URL env or http://localhost:$PROD_PORT)",
    )

    # config
    subparsers.add_parser("config", help="Validate and display configuration")

    # purge
    purge_parser = subparsers.add_parser("purge", help="Remove all embry0 Docker artifacts")
    purge_parser.add_argument("--containers", action="store_true", help="Remove only containers")
    purge_parser.add_argument("--images", action="store_true", help="Remove only images")
    purge_parser.add_argument("--volumes", action="store_true", help="Remove only volumes")
    purge_parser.add_argument("--networks", action="store_true", help="Remove only networks")

    # migrate-qa-config
    migrate_qa = subparsers.add_parser(
        "migrate-qa-config",
        help="Convert .embry0/qa.yaml from v1 → v2 schema",
    )
    migrate_qa.add_argument(
        "--qa-path",
        type=Path,
        default=Path(".embry0/qa.yaml"),
        help="Path to qa.yaml (default: .embry0/qa.yaml)",
    )
    migrate_qa.add_argument(
        "--app-name",
        type=str,
        default=None,
        help="App name in v2 apps: map (default: repo directory name)",
    )
    mode = migrate_qa.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print v2 to stdout; do not write")
    mode.add_argument("--write", action="store_true", help="Replace qa.yaml; back up v1 to qa.v1.yaml.bak")

    # build-qa-image
    bqi = subparsers.add_parser(
        "build-qa-image",
        help="Build a pre-baked QA sandbox image for a repo (Phase 2 cache).",
    )
    bqi.add_argument("--repo", required=True, help="GitHub repo in owner/name form")
    bqi.add_argument("--branch", default="main", help="Branch to build (default: main)")
    bqi.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if active tag's lockfile_sha matches current",
    )
    bqi.add_argument(
        "--api-url",
        default=None,
        help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)",
    )
    bqi.add_argument(
        "--api-key",
        default=None,
        help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)",
    )

    # onboard (EMB-50)
    onboard = subparsers.add_parser(
        "onboard",
        help="Analyze an existing repo and generate its qa.yaml into the external config store.",
    )
    onboard.add_argument("repo", help="GitHub repo in owner/name form")
    onboard.add_argument("--branch", default="main", help="Branch to analyze (default: main)")
    onboard.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip the boot/ready-check smoke phase (schema validation still applies)",
    )
    onboard.add_argument(
        "--api-url",
        default=None,
        help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)",
    )
    onboard.add_argument(
        "--api-key",
        default=None,
        help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)",
    )

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "build": cmd_build,
        "build-sandbox": cmd_build_sandbox,
        "health": cmd_health,
        "config": cmd_config,
        "purge": cmd_purge,
        "migrate-qa-config": cmd_migrate_qa_config,
        "build-qa-image": cmd_build_qa_image,
        "onboard": cmd_onboard,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

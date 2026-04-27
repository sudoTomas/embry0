"""Athanor CLI — manage the containerized production stack."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import structlog

logger = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────────────────

COMPOSE_FILE = "infra/docker-compose.yml"
ENV_FILE = ".env"
PROJECT_NAME = "legion"
DIND_CONTAINER = "legion-dind"
SANDBOX_IMAGE = "legion-sandbox:latest"

_SECRET_FIELDS = frozenset(
    {
        "github_token",
        "github_webhook_secret",
        "api_key",
        "anthropic_api_key",
        "claude_max_oauth_token",
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
    """Resolve the project root from ATHANOR_HOME or cwd."""
    home = Path(os.environ.get("ATHANOR_HOME", ".")).resolve()
    if not (home / COMPOSE_FILE).exists():
        _err(f"{COMPOSE_FILE} not found in {home}")
        _err("Set ATHANOR_HOME to the project root, e.g.:")
        _err("  export ATHANOR_HOME=/opt/legion")
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
    required = {"legion-frontend", "legion-orchestrator", "legion-dind", "legion-postgres"}
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
    return "legion-frontend" in images and "legion-orchestrator" in images


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
    from athanor.config import AthanorConfig

    try:
        config = AthanorConfig()
    except Exception as exc:
        _err(f"Error loading configuration: {exc}")
        sys.exit(1)

    issues: list[str] = []

    for field_name in sorted(AthanorConfig.model_fields):
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
    if config.provider_mode == "claude_max" and not config.claude_max_oauth_token:
        issues.append("CLAUDE_MAX_OAUTH_TOKEN is not set (required for provider_mode=claude_max)")
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
    """Check the health of a running Legion instance."""
    import httpx

    port = os.environ.get("PROD_PORT", "8200")
    url = args.url or os.environ.get("ATHANOR_URL", f"http://localhost:{port}")
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
        _info("Legion stack is already running")
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

    _header("Starting Legion stack...")
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

    _header("Legion is running")
    _run(_compose_cmd(home) + ["ps"])


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop the production stack."""
    home = _resolve_home()
    _header("Stopping Legion stack...")
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
        _err("DinD container is not running. Start the stack first: legion start")
        sys.exit(1)

    _build_sandbox(home)


def cmd_purge(args: argparse.Namespace) -> None:
    """Remove Legion Docker artifacts."""
    home = _resolve_home()
    purge_all = not (args.containers or args.images or args.volumes or args.networks)

    _header("Purging Legion Docker artifacts...")

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


# ── Argparse ─────────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point for the athanor CLI."""
    parser = argparse.ArgumentParser(
        prog="athanor",
        description="Athanor — manage the containerized production stack",
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
        help="Base URL (default: ATHANOR_URL env or http://localhost:$PROD_PORT)",
    )

    # config
    subparsers.add_parser("config", help="Validate and display configuration")

    # purge
    purge_parser = subparsers.add_parser("purge", help="Remove all Legion Docker artifacts")
    purge_parser.add_argument("--containers", action="store_true", help="Remove only containers")
    purge_parser.add_argument("--images", action="store_true", help="Remove only images")
    purge_parser.add_argument("--volumes", action="store_true", help="Remove only volumes")
    purge_parser.add_argument("--networks", action="store_true", help="Remove only networks")

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "build": cmd_build,
        "build-sandbox": cmd_build_sandbox,
        "health": cmd_health,
        "config": cmd_config,
        "purge": cmd_purge,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

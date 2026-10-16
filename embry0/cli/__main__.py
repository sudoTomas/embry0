"""embry0 CLI — manage the containerized production stack."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger()

# ── Constants ────────────────────────────────────────────────────────────────

COMPOSE_FILE = "infra/docker-compose.yml"
ENV_FILE = ".env"
# Compose's interpolation file. Its COMPOSE_FILE (paths relative to infra/,
# joined by ":") selects overlays such as docker-compose.rootless.yml (#268),
# exactly as infra/deploy.sh and the systemd unit read it.
INFRA_ENV_FILE = "infra/.env"
PROJECT_NAME = "embry0"
DIND_CONTAINER = "embry0-dind"

# What `embry0 start` and `embry0 build` build (#531). The `images`-profile
# services make the sandbox and proxy images that init-push-images pushes to
# the in-stack registry. It exits non-zero when one is missing from the host,
# and the orchestrator waits for it, so a build without them never starts the
# orchestrator. tests/unit/infra/test_image_wiring.py keeps this list equal to
# the compose services that build and to the push list.
STACK_SERVICES = ("orchestrator", "frontend")
IMAGE_BUILDER_SERVICES = (
    "sandbox-image-builder",
    "dev-python-image-builder",
    "dev-jvm-python-image-builder",
    "qa-image-builder",
    "proxy-image-builder",
)
BUILD_SERVICES = STACK_SERVICES + IMAGE_BUILDER_SERVICES

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
    print(f"{_YELLOW}⚠{_NC} {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"{_RED}✗{_NC} {msg}", file=sys.stderr)


def _header(msg: str) -> None:
    print(f"\n{_BOLD}{msg}{_NC}")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_home() -> Path:
    """Resolve the project root from EMBRY0_HOME or cwd."""
    home = Path(os.environ.get("EMBRY0_HOME", ".")).resolve()
    if not (home / COMPOSE_FILE).exists():
        _err(f"{COMPOSE_FILE} not found in {home}")
        _err("Set EMBRY0_HOME to the project root, e.g.:")
        _err("  export EMBRY0_HOME=/opt/embry0")
        sys.exit(1)
    return home


def _infra_env_value(home: Path, key: str) -> str:
    """``key``'s last value in infra/.env, unquoted, without sourcing the file; empty when unset."""
    path = home / INFRA_ENV_FILE
    if not path.exists():
        return ""
    value = ""
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            value = line[len(key) + 1 :].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _compose_files(home: Path) -> list[Path]:
    """The compose files the stack runs from: infra/.env's COMPOSE_FILE, else the base file alone."""
    selected = _infra_env_value(home, "COMPOSE_FILE")
    if not selected:
        return [home / COMPOSE_FILE]
    files = [home / "infra" / name for name in selected.split(":") if name]
    for path in files:
        if not path.exists():
            _err(f"COMPOSE_FILE in {INFRA_ENV_FILE} names {path.name}, which is not in {home / 'infra'}")
            sys.exit(1)
    return files


class _ComposeLoader(yaml.SafeLoader):
    """Reads compose's ``!override`` / ``!reset`` merge tags as the plain value."""


def _compose_tag(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(_tag, _compose_tag)


def _compose_services(home: Path) -> list[dict[str, Any]]:
    """Each selected compose file's ``services``, in order."""
    return [
        (yaml.load(path.read_text(), Loader=_ComposeLoader) or {}).get("services") or {}  # noqa: S506 — a SafeLoader
        for path in _compose_files(home)
    ]


def _sandbox_docker_host(home: Path) -> str | None:
    """The rootless sandbox daemon's DOCKER_HOST when the stack runs AGENT_RUNTIME=rootless, else None.

    Read from the orchestrator's environment in the selected compose files;
    a later file's value wins, as in compose.
    """
    env: dict[str, str] = {}
    for services in _compose_services(home):
        raw = (services.get("orchestrator") or {}).get("environment") or {}
        if isinstance(raw, list):
            raw = dict(str(item).split("=", 1) for item in raw if "=" in str(item))
        env.update({str(k): str(v) for k, v in raw.items()})
    if env.get("AGENT_RUNTIME") != "rootless":
        return None
    return env.get("DOCKER_HOST") or None


def _dind_enabled(home: Path) -> bool:
    """Whether the `dind` service runs: an overlay switches it off with a profile."""
    return not any((services.get("dind") or {}).get("profiles") for services in _compose_services(home))


def _sandbox_env(home: Path) -> dict[str, str] | None:
    """The environment for a docker command aimed at the sandbox daemon; None means the host's."""
    host = _sandbox_docker_host(home)
    if host is None:
        return None
    return {**os.environ, "DOCKER_HOST": host}


def _compose_cmd(home: Path) -> list[str]:
    """Build the base docker compose command with project name and env file."""
    cmd = ["docker", "compose"]
    for path in _compose_files(home):
        cmd.extend(["-f", str(path)])
    cmd.extend(["-p", PROJECT_NAME])
    env_path = home / ENV_FILE
    if env_path.exists():
        cmd.extend(["--env-file", str(env_path)])
        # The overlay's own variables (SANDBOX_DOCKER_GID) live in infra/.env
        # next to the COMPOSE_FILE that selected it; read after the root file.
        if _infra_env_value(home, "COMPOSE_FILE"):
            cmd.extend(["--env-file", str(home / INFRA_ENV_FILE)])
    return cmd


def _build_cmd(home: Path, services: tuple[str, ...] = BUILD_SERVICES) -> list[str]:
    """The compose build of *services*; the image builders are in the `images` profile."""
    return _compose_cmd(home) + ["--profile", "images", "build", *services]


def _builds(
    home: Path, services: tuple[str, ...] = BUILD_SERVICES, extra: tuple[str, ...] = ()
) -> list[tuple[list[str], dict[str, str] | None]]:
    """The build commands for *services*, each with the environment to run it in.

    Under AGENT_RUNTIME=rootless the sandbox and proxy images are built on the
    rootless sandbox daemon, which runs them by local tag, and the rest on the
    host's; otherwise one build on the host, as before.
    """
    sandbox_env = _sandbox_env(home)
    if sandbox_env is None:
        return [(_build_cmd(home, services) + list(extra), None)]
    on_host = tuple(name for name in services if name not in IMAGE_BUILDER_SERVICES)
    on_sandbox = tuple(name for name in services if name in IMAGE_BUILDER_SERVICES)
    builds: list[tuple[list[str], dict[str, str] | None]] = []
    if on_host:
        builds.append((_build_cmd(home, on_host) + list(extra), None))
    if on_sandbox:
        builds.append((_build_cmd(home, on_sandbox) + list(extra), sandbox_env))
    return builds


def _built_images(home: Path) -> list[str]:
    """The image each BUILD_SERVICES service produces, read from the compose file.

    A service with no `image:` gets compose's default name, `<project>-<service>`.
    """
    services = yaml.safe_load((home / COMPOSE_FILE).read_text())["services"]
    return [services[name].get("image") or f"{PROJECT_NAME}-{name}" for name in BUILD_SERVICES]


def _run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command; ``env`` replaces the environment when given."""
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, env=env)


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
    required = {"embry0-frontend", "embry0-orchestrator", "embry0-postgres"}
    if _dind_enabled(home):
        required.add(DIND_CONTAINER)
    return required.issubset(running)


def _images_exist(home: Path) -> bool:
    """Check that every image the stack builds is on the host daemon.

    Not `compose images`: that lists the images of existing containers, so a
    stack whose orchestrator and frontend were built without the sandbox
    images (the pre-#531 `embry0 start`) passed it and was never rebuilt.
    """
    images = dict(zip(BUILD_SERVICES, _built_images(home), strict=True))
    sandbox_env = _sandbox_env(home)
    if sandbox_env is None:
        result = _run(["docker", "image", "inspect", *images.values()], check=False, capture=True)
        return result.returncode == 0
    # Under rootless the sandbox and proxy images live on the sandbox daemon.
    on_host = [image for name, image in images.items() if name not in IMAGE_BUILDER_SERVICES]
    on_sandbox = [image for name, image in images.items() if name in IMAGE_BUILDER_SERVICES]
    host = _run(["docker", "image", "inspect", *on_host], check=False, capture=True)
    sandbox = _run(["docker", "image", "inspect", *on_sandbox], check=False, capture=True, env=sandbox_env)
    return host.returncode == 0 and sandbox.returncode == 0


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


def _wait_for_sandbox_daemon(host: str, timeout: int = 60) -> bool:
    """Poll the rootless sandbox daemon until it answers or timeout."""
    _info("Waiting for the sandbox daemon to be ready...")
    for _ in range(timeout):
        result = _run(["docker", "info"], check=False, capture=True, env={**os.environ, "DOCKER_HOST": host})
        if result.returncode == 0:
            _info("The sandbox daemon is answering")
            return True
        time.sleep(1)
    _err(f"The sandbox daemon on {host} is not answering after {timeout}s (docs/ops/provisioning.md section 10)")
    return False


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_config(args: argparse.Namespace) -> None:
    """Validate and display current configuration."""
    from embry0.config import Embry0Config
    from embry0.config_redaction import mask_url_credentials, redacted_settings

    try:
        config = Embry0Config()
    except Exception as exc:
        # A validator may quote the value it refused, URL and all.
        _err(f"Error loading configuration: {mask_url_credentials(str(exc))}")
        sys.exit(1)

    from embry0.config_lint import declared_env_names, find_unknown_env_vars

    issues: list[str] = []

    # #535: secrets and URL passwords are masked by the one rule every
    # renderer of the settings model shares (embry0/config_redaction.py).
    for field_name, display in redacted_settings(config):
        print(f"{field_name}={display}")

    # #248: Embry0Config ignores unknown env vars, so a typo is otherwise silent.
    for unknown in find_unknown_env_vars(os.environ, declared_env_names(Embry0Config)):
        hint = f" — did you mean {unknown.did_you_mean}?" if unknown.did_you_mean else ""
        _warn(f"{unknown.name} is set but is not an embry0 setting{hint}")

    if not config.github_token:
        issues.append("GITHUB_TOKEN is not set")
    # RAV-1239: provider_mode is gone — Anthropic agents run under whatever
    # auth_mode resolves per repo/job/agent, so at least one credential must
    # be present for any Anthropic-backed agent to run.
    if not config.anthropic_api_key and not config.claude_code_oauth_token:
        issues.append(
            "Neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set (Anthropic-backed agents cannot run)"
        )

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
        _header("Building images (the stack, and the sandbox and proxy images jobs run in)...")
        for cmd, build_env in _builds(home):
            _run(cmd, env=build_env)

    _header("Starting embry0 stack...")
    env = os.environ.copy()
    if args.port:
        env["PROD_PORT"] = str(args.port)
    if args.host:
        env["HOST"] = args.host
    subprocess.run(_compose_cmd(home) + ["up", "-d"], check=True, env=env)

    sandbox_host = _sandbox_docker_host(home)
    if sandbox_host is not None:
        if not _wait_for_sandbox_daemon(sandbox_host):
            _err("Stack started but the sandbox daemon is not answering")
            sys.exit(1)
    elif not _wait_for_dind():
        _err("Stack started but DinD failed health check")
        sys.exit(1)

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
    extra = () if args.cached else ("--no-cache",)
    _header("Building production images..." + (" (cached)" if args.cached else " (clean)"))
    for cmd, build_env in _builds(home, extra=extra):
        _run(cmd, env=build_env)
    _info("Build complete")


def cmd_build_sandbox(args: argparse.Namespace) -> None:
    """Rebuild the sandbox and proxy images on the host (the rootless sandbox daemon under that overlay).

    Jobs run them from the in-stack registry, by the digest init-push-images
    recorded, so a rebuild is used only after that service pushes it again and
    the orchestrator restarts. That ends running jobs, so it is printed, not run.
    """
    home = _resolve_home()
    _header("Building the sandbox and proxy images...")
    for cmd, build_env in _builds(home, IMAGE_BUILDER_SERVICES):
        _run(cmd, env=build_env)
    if _sandbox_docker_host(home) is not None:
        # Built on the rootless sandbox daemon, which runs them by local tag:
        # there is no registry to push to, only the orchestrator to restart.
        restart = _compose_cmd(home) + ["up", "-d", "--force-recreate", "orchestrator"]
        _info("Built on the sandbox daemon. To run jobs on them, restart the orchestrator (ends running jobs):")
        print(f"  {shlex.join(restart)}")
        return
    push = _compose_cmd(home) + ["up", "-d", "--force-recreate", "init-push-images", "orchestrator"]
    _info("Built. To run jobs on them, push and pin them and restart the orchestrator (ends running jobs):")
    print(f"  {shlex.join(push)}")


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

    _info(
        f"migrated {args.qa_path} (v1 backed up to qa.v1.yaml.bak). "
        "Store the result in embry0 (target repos carry no config file): "
        "PUT /api/v1/repos/<owner>/<repo>/qa-config with the migrated text, "
        "or paste it into the dashboard config editor."
    )


def cmd_merge_sync(args: argparse.Namespace) -> None:
    """Handle `embry0 merge-sync check|backfill` (#279)."""
    import asyncio

    from embry0.cli import merge_sync

    # stdout is the report (a `--json` one gets parsed); the sweep's own log
    # lines belong on stderr.
    with merge_sync.logs_to_stderr():
        report = asyncio.run(merge_sync.run(args.action))
    print(merge_sync.render(args.action, report, as_json=args.json))
    sys.exit(merge_sync.exit_code(args.action, report))


def cmd_credentials(args: argparse.Namespace) -> None:
    """Handle `embry0 credentials import-env` (spec 2026-09-29 §3.10).

    Thin API client of POST /api/v1/credentials/import-env: moves forge
    tokens and LLM accounts from the orchestrator environment into the
    registry. Exits 0 when nothing was skipped, 2 when something was,
    1 on failure.
    """
    import json

    import httpx

    if args.action != "import-env":
        _err(f"unknown credentials action {args.action!r}")
        sys.exit(1)
    base = (args.api_url or os.environ.get("EMBRY0_API_URL") or "http://localhost:8200").rstrip("/")
    key = _resolve_api_key(None)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        resp = httpx.post(
            f"{base}/api/v1/credentials/import-env",
            json={"replace": bool(args.replace)},
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
    except httpx.ConnectError:
        _err(f"cannot reach the orchestrator at {base} — is the stack running? (embry0 start)")
        sys.exit(1)
    if resp.status_code != 200:
        _err(f"import-env failed: HTTP {resp.status_code} {resp.text[:300]}")
        sys.exit(1)

    report = resp.json()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for field in ("created", "bound", "skipped"):
            print(f"{field}: " + (", ".join(report.get(field, [])) or "-"))
    sys.exit(2 if report.get("skipped") else 0)


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
            f"onboard completed — the qa.yaml for {args.repo} is stored in the embry0 DB. "
            f"Inspect: GET {base}/api/v1/repos/{args.repo}/qa-config"
        )
    else:
        _err(f"onboard {status}: {(job.get('error_message') or 'no detail')[:1000]}")
        sys.exit(1)


def cmd_review(args: argparse.Namespace) -> None:
    """Handle ``embry0 review <repo> <number>`` (#30).

    Thin API client: POST /api/v1/jobs {pipeline: "pr-review"}. Reviews one
    pull request now, whether or not the repo has review turned on; every
    other gate (embry0's own pull request, budget) still applies. Prints the
    job and does not wait: the review lands on the pull request itself.
    """
    import httpx

    base = (args.api_url or os.environ.get("EMBRY0_API_URL") or "http://localhost:8200").rstrip("/")
    key = _resolve_api_key(args.api_key)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.post(
            f"{base}/api/v1/jobs",
            json={"pipeline": "pr-review", "repo": args.repo, "number": args.number},
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
        _err(f"review not started ({resp.status_code}): {resp.text[:500]}")
        sys.exit(2)
    job_id = resp.json().get("job_id")
    _info(f"review job {job_id} started for {args.repo}#{args.number}; follow it at {base}/jobs/{job_id}")


def cmd_init(args: argparse.Namespace) -> None:
    """Handle ``embry0 init`` (EMB-49) — one-command new-project bootstrap.

    Thin API client of POST /api/v1/repos/bootstrap (token routing + the
    config store live in the orchestrator).
    """
    import httpx

    base = (args.api_url or os.environ.get("EMBRY0_API_URL") or "http://localhost:8200").rstrip("/")
    key = _resolve_api_key(args.api_key)
    headers = {"X-Requested-With": "XMLHttpRequest"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload: dict[str, Any] = {"repo": args.repo, "force": args.force}
    for field in ("app", "boot_command", "frontend_url", "ready_check_url", "sandbox_profile"):
        val = getattr(args, field)
        if val:
            payload[field] = val

    try:
        resp = httpx.post(
            f"{base}/api/v1/repos/bootstrap",
            json=payload,
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
        detail = resp.text[:500]
        try:
            detail = resp.json().get("detail", detail)
        except Exception:  # noqa: BLE001
            pass
        _err(f"bootstrap failed ({resp.status_code}): {detail}")
        sys.exit(1)

    result = resp.json()
    _info(f"bootstrapped {result['repo']}:")
    for step in result.get("steps", []):
        _info(f"  - {step}")
    _info("")
    _info("Next steps:")
    if result.get("qa_required") == "never":
        _info("  - QA is set to `never` (empty repo). Once there is an app to boot,")
        _info(f"    edit the stored config (PUT /api/v1/repos/{result['repo']}/qa-config)")
        _info("    or run `embry0 onboard` to regenerate it, and flip qa_required to auto.")
    _info("  - File the first issue: label it `embry0` in Linear (team EMB), or")
    _info(f'    POST /api/v1/issues {{repo: "{result["repo"]}", auto_triage: true}} —')
    _info("    the pipeline picks it up from there.")


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
    subparsers.add_parser("build-sandbox", help="Rebuild the sandbox and proxy images on the host")

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
    # merge-sync
    merge_sync_p = subparsers.add_parser(
        "merge-sync",
        help="Compare recorded PR delivery with GitHub (check) or correct all history (backfill)",
    )
    merge_sync_p.add_argument("action", choices=["check", "backfill"])
    merge_sync_p.add_argument("--json", action="store_true", help="print the full report as JSON")

    # credentials
    creds = subparsers.add_parser("credentials", help="Credential registry operations")
    creds_sub = creds.add_subparsers(dest="action", required=True)
    imp = creds_sub.add_parser(
        "import-env",
        help="Import forge tokens and LLM accounts from the orchestrator environment",
    )
    imp.add_argument(
        "--replace", action="store_true", help="Overwrite a registry secret that differs from the env value"
    )
    imp.add_argument("--json", action="store_true", help="Print the report as JSON")
    imp.add_argument(
        "--api-url",
        default=None,
        help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)",
    )

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

    # init (EMB-49)
    init_p = subparsers.add_parser(
        "init",
        help="One-command bootstrap: verify GitHub access + register a starter QA config for a new repo.",
    )
    init_p.add_argument("repo", help="GitHub repo in owner/name form")
    init_p.add_argument("--app", default=None, help="Declare an app skeleton (lowercase slug)")
    init_p.add_argument("--boot-command", default=None, help="App boot command (default: npm ci && npm run dev)")
    init_p.add_argument("--frontend-url", default=None, help="App URL in-sandbox (default: http://localhost:3000)")
    init_p.add_argument("--ready-check-url", default=None, help="Ready-check URL (default: frontend URL)")
    init_p.add_argument("--sandbox-profile", default=None, help="Seed repo_preferences with this sandbox profile")
    init_p.add_argument("--force", action="store_true", help="Overwrite an existing stored config")
    init_p.add_argument(
        "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
    )
    init_p.add_argument(
        "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
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

    # review (#30)
    review_p = subparsers.add_parser(
        "review",
        help="Review one pull request (or merge request) now and post the findings on it.",
    )
    review_p.add_argument(
        "repo", help="Repository as registered in embry0 (owner/name, or a GitLab group/project path)"
    )
    review_p.add_argument("number", type=int, help="Pull request number (GitLab: the merge request iid)")
    review_p.add_argument(
        "--api-url", default=None, help="Orchestrator base URL (default: EMBRY0_API_URL or http://localhost:8200)"
    )
    review_p.add_argument(
        "--api-key", default=None, help="Bearer key (default: EMBRY0_API_KEY / API_KEY env, then ./.env)"
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
        "merge-sync": cmd_merge_sync,
        "credentials": cmd_credentials,
        "build-qa-image": cmd_build_qa_image,
        "onboard": cmd_onboard,
        "review": cmd_review,
        "init": cmd_init,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

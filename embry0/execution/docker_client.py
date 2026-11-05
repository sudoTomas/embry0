"""DinD-aware Docker CLI wrapper.

All Docker operations go through the CLI, respecting DOCKER_HOST and TLS
settings for Docker-in-Docker communication.
"""

import asyncio
import contextlib
import json
import os
import signal
from typing import Any, cast

import structlog

logger = structlog.get_logger(__name__)

# Every container embry0 starts on the sandbox daemon carries these, so the
# daemon's objects can be told apart by label, not by name alone (#268).
MANAGED_LABEL = "embry0.managed=true"
SANDBOX_ROLE_LABEL = "embry0.role=sandbox"
PROXY_ROLE_LABEL = "embry0.role=proxy"


# Per-line ceiling for stream_exec stdout. The sandbox runner prints its
# final_result as ONE JSON line carrying the full messages transcript, so a
# watcher analysis job's line runs to hundreds of KB — far past asyncio's
# 64 KiB default, which killed those jobs after the agent had finished with
# "Separator is found, but chunk is longer than limit". 16 MiB matches
# mcp_client._STREAM_LIMIT.
_STREAM_LINE_LIMIT = 16 * 1024 * 1024

# Ceiling on handing stream_exec's stdin payload to the command. The payload
# is read in one go at process start, so this only trips on a wedged exec.
_STDIN_WRITE_TIMEOUT_S = 60.0

# Env-var keys whose values must never reach logs. The docker run command
# passes credentials/tokens via `-e KEY=VAL` argv pairs (sandbox launch
# threads CLAUDE_CODE_OAUTH_TOKEN, the per-sandbox EMBRY0_SANDBOX_TOKEN,
# etc.) and the same argv shows up at debug level on docker_cmd, at
# warning level on docker_cmd_timeout, and at error level on
# docker_cmd_failed. Scrub before logging.
# Non-secret env keys whose values are safe + useful to keep in logs. Every other
# `-e KEY=VAL` value is redacted (default-deny), so a newly-injected secret is
# scrubbed automatically. The previous allowlist leaked AZURE / CF / DB / CRON /
# HUB / REPORTS secrets because they were never added to it.
_SAFE_ENV_KEYS = frozenset(
    {
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    }
)


def _scrub_cmd_for_log(cmd: list[str]) -> list[str]:
    """Return a copy of ``cmd`` with secret ``-e KEY=VAL`` values redacted
    (and the sandbox runner's ``--config <json>`` value, see below).

    Walks the argv looking for the docker `-e` flag followed by a
    ``KEY=VAL`` argument. If ``KEY`` is NOT in ``_SAFE_ENV_KEYS`` (default-deny),
    the value is replaced with ``***REDACTED***``. Any other argv token is preserved
    verbatim. Order and length are preserved so structured log consumers
    that index into the array see the same shape.
    """
    scrubbed: list[str] = []
    i = 0
    while i < len(cmd):
        token = cmd[i]
        if token == "-e" and i + 1 < len(cmd):
            kv = cmd[i + 1]
            key, sep, _ = kv.partition("=")
            if sep and key not in _SAFE_ENV_KEYS:
                scrubbed.append(token)
                scrubbed.append(f"{key}=***REDACTED***")
                i += 2
                continue
        if token == "--config" and i + 1 < len(cmd):
            # #304 stale-image fallback: the sandbox runner's whole invocation
            # (prompt included) rides this one argv value.
            scrubbed.extend([token, "***REDACTED***"])
            i += 2
            continue
        scrubbed.append(_elide(token))
        i += 1
    return scrubbed


# A long shell script (embry0's git prelude is several KB) is logged by its
# start and its end, where the step's own commands are.
_LOG_TOKEN_HEAD = 160
_LOG_TOKEN_TAIL = 1200


def _elide(token: str) -> str:
    if len(token) <= _LOG_TOKEN_HEAD + _LOG_TOKEN_TAIL + 64:
        return token
    cut = len(token) - _LOG_TOKEN_HEAD - _LOG_TOKEN_TAIL
    return f"{token[:_LOG_TOKEN_HEAD]}…[{cut} chars]…{token[-_LOG_TOKEN_TAIL:]}"


class ExecStdinTimeoutError(RuntimeError):
    """stream_exec's command never read its stdin payload (a wedged exec)."""


class OutputTooLargeError(RuntimeError):
    """A docker command printed more than its byte cap to stdout; the command was stopped.

    A RuntimeError, so a caller's ``except RuntimeError`` fails its step as it
    does for any other failed command.
    """


#: The stdout a docker command may print before it is stopped (run_cmd and
#: run_cmd_bytes), unless its caller names another cap. Nearly every caller
#: reads a short answer: an id, an inspect field, a marker line, a small file
#: or listing written in a sandbox. 4 MiB is far above all of those and still
#: bounds what one command can make the orchestrator hold; a caller that reads
#: more names a larger cap.
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
#: The cap for a listing of the daemon's own objects (containers, images,
#: volumes, networks, firewall rules). Its size follows how many objects the
#: daemon holds, not what one process prints; 64 MiB is far past any real one.
DAEMON_LISTING_MAX_BYTES = 64 * 1024 * 1024
#: The largest cap any caller may name. A cap is a promise about how much the
#: orchestrator holds for one command; past this one it no longer bounds
#: anything worth having, so it is refused rather than trusted.
MAX_OUTPUT_CAP_BYTES = 256 * 1024 * 1024
#: The stdout kept by a command whose output is only a log (``keep_tail``): the
#: cause of a failure is at its end, and run_cmd's error quotes the last 2000
#: characters.
LOG_TAIL_BYTES = 64 * 1024

#: How much the reader asks a pipe for at a time.
_READ_CHUNK = 64 * 1024
#: How much of a command's stderr is kept from its start, and from its end; the
#: rest is read and dropped. The end is where a failing build or install says why.
_STDERR_HEAD = 64 * 1024
_STDERR_TAIL = 16 * 1024


def _check_cap(max_bytes: int) -> None:
    """Refuse a cap that is not 1..MAX_OUTPUT_CAP_BYTES bytes: no command reads without a real one."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 0 < max_bytes <= MAX_OUTPUT_CAP_BYTES:
        raise ValueError(f"max_bytes must be 1..{MAX_OUTPUT_CAP_BYTES} bytes, not {max_bytes!r}")


class _Kept:
    """What is kept of one pipe: up to ``head`` bytes from its start and ``tail`` from its end."""

    def __init__(self, *, head: int = 0, tail: int = 0) -> None:
        self._head_room = head
        self._tail_room = tail
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_room - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk and self._tail_room:
            self.tail += chunk
            del self.tail[: max(0, len(self.tail) - self._tail_room)]

    def data(self) -> bytes:
        """The kept bytes, with a line saying how many were dropped where they were."""
        dropped = self.total - len(self.head) - len(self.tail)
        if dropped <= 0:
            return bytes(self.head + self.tail)
        return bytes(self.head) + f"\n[... {dropped} bytes not kept ...]\n".encode() + bytes(self.tail)


async def _drain(pipe: asyncio.StreamReader, kept: _Kept, limit: int | None) -> None:
    """Read ``pipe`` to its end into ``kept``.

    With ``limit`` the pipe is never asked for more than ``limit + 1`` bytes in
    all, and :class:`OutputTooLargeError` is raised once it has handed over more
    than ``limit``.
    """
    while True:
        want = _READ_CHUNK if limit is None else min(_READ_CHUNK, limit + 1 - kept.total)
        chunk = await pipe.read(want)
        if not isinstance(chunk, bytes):
            # Only a stand-in pipe does this; looping on it would never reach EOF.
            raise TypeError(f"a pipe read returned {type(chunk).__name__}, not bytes")
        if not chunk:
            return
        kept.add(chunk)
        if limit is not None and kept.total > limit:
            raise OutputTooLargeError(f"command output passed its {limit}-byte cap; the command was stopped")


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    """Write ``data`` to a command's stdin and close it.

    A command that exits without reading it is not an error, as with
    ``communicate()``: its exit code and stderr say what happened.
    """
    try:
        stdin.write(data)
        await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    stdin.close()


async def _stop(proc: asyncio.subprocess.Process, readers: list[asyncio.Future[None]], *, kill_group: bool) -> bool:
    """Kill ``proc`` (its whole group with ``kill_group``), stop its readers and reap it; True when reaped in time.

    With ``kill_group``, ``proc`` must have been started with
    ``start_new_session=True``, so its group holds nothing else. Killing only
    ``proc`` is not enough there: a child it started (the compose plugin
    ``docker compose`` runs, say) keeps the stdout pipe open (#334).
    """
    if kill_group:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
    elif proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    for reader in readers:
        reader.cancel()
    await asyncio.gather(*readers, return_exceptions=True)
    # A reader stopped with data still in the pipe leaves that pipe paused short
    # of EOF, and wait() also waits for every pipe to close: close them before
    # waiting, or the wait runs out its 5 s on a process that is already dead.
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        transport.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        return False
    return True


async def collect_output(
    proc: asyncio.subprocess.Process,
    *,
    timeout: float,
    max_bytes: int,
    keep_tail: bool = False,
    stdin_data: bytes | None = None,
    kill_group: bool = False,
    stuck_event: str = "docker_cmd_kill_stuck",
    log_fields: dict[str, Any] | None = None,
) -> tuple[bytes, bytes]:
    """Feed ``proc`` its ``stdin_data``, read its stdout and stderr in chunks and reap it: ``(stdout, stderr)``.

    The one reader of a docker command's output (run_cmd, run_cmd_bytes,
    exec_with_stdin, the runtime's compose listing). It never holds more than
    ``max_bytes`` of stdout: past that the command is stopped and
    :class:`OutputTooLargeError` raised, or, with ``keep_tail`` (a command
    whose output is only a log), the oldest bytes are dropped and the last
    ``max_bytes`` returned. stderr keeps its first 64 KiB and its last 16 KiB;
    the rest is read and dropped, so the command never blocks on a full pipe.

    On the cap, on ``timeout`` (TimeoutError) and on cancellation the command
    is killed (its whole process group with ``kill_group``, for a process
    started with ``start_new_session=True``), its readers stopped and it is
    reaped before the exception propagates; one that cannot be reaped in time
    is logged as ``stuck_event``. ``proc.returncode`` is set once this returns.
    """
    _check_cap(max_bytes)
    if proc.stdout is None:
        raise ValueError("collect_output reads a process started with stdout=PIPE")
    out = _Kept(tail=max_bytes) if keep_tail else _Kept(head=max_bytes)
    err = _Kept(head=_STDERR_HEAD, tail=_STDERR_TAIL)
    tasks: list[asyncio.Future[None]] = [
        asyncio.ensure_future(_drain(proc.stdout, out, None if keep_tail else max_bytes))
    ]
    if proc.stderr is not None:
        tasks.append(asyncio.ensure_future(_drain(proc.stderr, err, None)))
    if stdin_data is not None and proc.stdin is not None:
        tasks.append(asyncio.ensure_future(_feed(proc.stdin, stdin_data)))
    try:
        async with asyncio.timeout(timeout):
            for task in tasks:
                await task
            await proc.wait()
    except BaseException:
        if not await _stop(proc, tasks, kill_group=kill_group):
            logger.error(stuck_event, **{**(log_fields or {}), "pid": proc.pid})
        raise
    return out.data(), err.data()


class DockerClient:
    """Wraps Docker CLI commands for DinD environments."""

    def __init__(
        self,
        docker_host: str = "",
        tls_verify: bool = False,
        cert_path: str = "",
    ) -> None:
        self._host = docker_host
        self._tls_verify = tls_verify
        self._cert_path = cert_path

    @staticmethod
    def _registry_qualified(image: str) -> bool:
        """True when ``image`` is qualified against a registry host —
        first path segment contains ``.``/``:`` or is ``localhost``
        (registry:5000/..., ghcr.io/..., 1.2.3.4:5000/...). Only then is
        ``--pull=always`` valid; a bare local-only name would fail to pull.
        """
        first_segment = image.split("/", 1)[0] if "/" in image else ""
        return bool(first_segment) and ("." in first_segment or ":" in first_segment or first_segment == "localhost")

    def _build_base_cmd(self) -> list[str]:
        """Build base docker command with host and TLS flags."""
        cmd = ["docker"]
        if self._host:
            cmd.extend(["--host", self._host])
        if self._tls_verify and self._cert_path:
            cmd.append("--tlsverify")
            cmd.append(f"--tlscacert={self._cert_path}/ca.pem")
            cmd.append(f"--tlscert={self._cert_path}/cert.pem")
            cmd.append(f"--tlskey={self._cert_path}/key.pem")
        return cmd

    def build_run_cmd(
        self,
        image: str,
        name: str,
        network: str = "sandbox-restricted",
        memory: str = "8g",
        cpus: str = "4",
        pids_limit: int = 512,
        cap_drop: list[str] | None = None,
        cap_add: list[str] | None = None,
        security_opt: list[str] | None = None,
        read_only: bool = True,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        tmpfs_mounts: list[str] | None = None,
        extra_hosts: dict[str, str] | None = None,
        job_id: str | None = None,
    ) -> list[str]:
        """Build `docker run -d` command for sandbox container.

        The container is labelled ``embry0.managed=true`` and
        ``embry0.role=sandbox``, and ``embry0.job_id=<job_id>`` when given.

        ``extra_hosts`` maps hostname → IP for ``--add-host=name:ip`` flags;
        used by SandboxManager to publish the backend IPs of host-side proxies
        (minio-proxy, presign-proxy) into a DinD-spawned sandbox's /etc/hosts
        so the sandbox can reach those proxies by name.

        ``tmpfs_mounts`` is a list of in-container paths to mount as
        ephemeral tmpfs (rw, nosuid). Each becomes a ``--tmpfs <path>:rw,nosuid``
        flag. Used by sub-task launches to overlay an empty tmpfs on top of
        a shared cache volume (e.g. ``/workspace/.qa``) so the per-sub-task
        directory isn't shared across containers — without it, all 14 fan-out
        sub-tasks would write the SAME ``/workspace/.qa/result.json`` and
        the last writer wins (silent test poisoning).
        """
        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d", "--init"])
        # Registry-qualified image => force a pull on every launch. This keeps
        # DinD from serving stale `latest` after the host has rebuilt and
        # init-push-images has refreshed the registry — a previous silent
        # failure mode where the inner docker daemon's cached layers won an
        # implicit race against the registry's newer digest.
        if self._registry_qualified(image):
            cmd.append("--pull=always")
        cmd.extend(["--name", name])
        cmd.extend(["--label", MANAGED_LABEL, "--label", SANDBOX_ROLE_LABEL])
        if job_id:
            cmd.extend(["--label", f"embry0.job_id={job_id}"])
        cmd.append(f"--network={network}")
        cmd.append(f"--memory={memory}")
        cmd.append(f"--cpus={cpus}")
        cmd.append(f"--pids-limit={pids_limit}")
        cmd.append(f"--memory-swap={memory}")  # equal to --memory => no swap
        cmd.append("--ulimit=nofile=4096:8192")

        for cap in cap_drop or ["ALL"]:
            cmd.append(f"--cap-drop={cap}")
        for cap in cap_add or []:
            cmd.append(f"--cap-add={cap}")
        for opt in security_opt or ["no-new-privileges"]:
            cmd.append(f"--security-opt={opt}")

        if read_only:
            cmd.append("--read-only")
            cmd.extend(["--tmpfs", "/tmp:rw,nosuid"])
            cmd.extend(["--tmpfs", "/home/agent/.claude:rw,nosuid"])

        for path in tmpfs_mounts or []:
            # mode=1777 makes the tmpfs root world-writable + sticky (same
            # default as /tmp). Without it Docker mounts the tmpfs at mode
            # 0755 owned by root, and a non-root sandbox user (the `agent`
            # account in the sandbox image) cannot create files in it —
            # `mkdir /workspace/.qa/logs` errors with `Permission denied`
            # and the QA fan-out aborts every sub-task with infra_failure.
            cmd.extend(["--tmpfs", f"{path}:rw,nosuid,mode=1777"])

        for host, ip in (extra_hosts or {}).items():
            cmd.append(f"--add-host={host}:{ip}")

        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])

        for vol in volumes or []:
            cmd.extend(["-v", vol])

        cmd.extend([image, "sleep", "infinity"])
        return cmd

    def build_run_proxy_cmd(
        self,
        *,
        name: str,
        image: str,
        network: str,
        env: dict[str, str],
    ) -> tuple[list[str], str]:
        """Build `docker run -d` for a proxy container, returning (cmd, env_file_path).

        Proxies hold the orchestrator's credentials. Pass them via --env-file
        (mode 0600) instead of -e KEY=VAL argv to keep them out of host
        `ps -ef` output. Caller is responsible for unlinking env_file_path
        after `docker run` returns (the container has snapshotted its env).
        """
        import os
        import tempfile

        fd, env_file_path = tempfile.mkstemp(prefix=f"embry0-proxy-env-{name}-", suffix=".env")
        try:
            with os.fdopen(fd, "w") as f:
                for key, value in env.items():
                    # No quoting: docker --env-file does not interpret quotes;
                    # value is taken literally up to newline.
                    f.write(f"{key}={value}\n")
            os.chmod(env_file_path, 0o600)
        except Exception:
            try:
                os.unlink(env_file_path)
            except OSError:
                pass
            raise

        cmd = self._build_base_cmd()
        cmd.extend(["run", "-d"])
        # Same stale-`latest` guard as build_run_cmd. Proxies missed this flag
        # and DinD silently ran a 2026-05-04 cached embry0-proxy for two
        # months — the pushed per-owner-token proxy never started until an
        # access smoke test exposed it (2026-07-06).
        if self._registry_qualified(image):
            cmd.append("--pull=always")
        cmd.extend(["--name", name])
        cmd.extend(["--label", MANAGED_LABEL, "--label", PROXY_ROLE_LABEL])
        cmd.append(f"--network={network}")
        cmd.extend(["--env-file", env_file_path])
        cmd.append(image)
        return cmd, env_file_path

    def build_exec_cmd(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        interactive: bool = False,
        user: str | None = None,
    ) -> list[str]:
        """Build `docker exec` command. ``interactive`` adds ``-i`` (stdin attached).

        ``user`` is ``docker exec -u``. The image's USER is the unprivileged
        agent; a named volume is root-owned until something chowns it.
        """
        cmd = self._build_base_cmd()
        cmd.append("exec")
        if interactive:
            cmd.append("-i")
        if user:
            cmd.extend(["-u", user])
        if workdir:
            cmd.extend(["-w", workdir])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(container)
        cmd.extend(command)
        return cmd

    def build_inspect_image_cmd(self, container: str) -> list[str]:
        """Build `docker inspect` printing the image ref a container runs."""
        return [*self._build_base_cmd(), "inspect", "--format", "{{.Config.Image}}", container]

    def build_inspect_running_cmd(self, container: str) -> list[str]:
        """Build `docker inspect` printing whether a container is running (``true``/``false``)."""
        return [*self._build_base_cmd(), "inspect", "--format", "{{.State.Running}}", container]

    def build_inspect_has_env_cmd(self, container: str, entry: str) -> list[str]:
        """Build `docker inspect` printing ``yes`` when the container's env holds ``entry`` (``KEY=value``).

        Only that answer leaves the daemon: the rest of the env, credentials
        among it, is never printed.
        """
        if '"' in entry or "{" in entry or "}" in entry:
            raise ValueError("env entry must not contain quotes or braces")
        fmt = f'{{{{range .Config.Env}}}}{{{{if eq . "{entry}"}}}}yes{{{{end}}}}{{{{end}}}}'
        return [*self._build_base_cmd(), "inspect", "--format", fmt, container]

    def build_inspect_id_cmd(self, container: str) -> list[str]:
        """Build `docker inspect` printing a container's full ID, from its name or a short ID."""
        return [*self._build_base_cmd(), "inspect", "--format", "{{.Id}}", container]

    def build_stop_cmd(self, container: str, timeout: int = 10) -> list[str]:
        """Build `docker stop` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["stop", "-t", str(timeout), container])
        return cmd

    def build_rm_cmd(self, container: str) -> list[str]:
        """Build `docker rm` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["rm", "-f", container])
        return cmd

    def build_network_cmd(self, action: str, network: str, container: str) -> list[str]:
        """Build `docker network connect/disconnect` command."""
        cmd = self._build_base_cmd()
        cmd.extend(["network", action, network, container])
        return cmd

    async def inspect_network(self, name: str) -> dict[str, Any]:
        """Return parsed `docker network inspect <name>` output.

        Raises RuntimeError if the network does not exist or inspect fails.
        """
        cmd = self._build_base_cmd()
        cmd.extend(["network", "inspect", name])
        # The inspect lists every container attached to the network.
        output = await self.run_cmd(cmd, timeout=15, max_bytes=DAEMON_LISTING_MAX_BYTES)
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"docker network inspect {name} returned non-JSON: {output[:200]}") from exc
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"docker network inspect {name} returned empty list")
        return cast(dict[str, Any], data[0])

    async def assert_sandbox_networks_or_die(self) -> None:
        """Verify sandbox-restricted has masquerade disabled and sandbox-internet exists.

        Refuses to return on misconfiguration. Called from app startup before
        any proxy launches; failure means the orchestrator does not start.
        """
        try:
            restricted = await self.inspect_network("sandbox-restricted")
        except RuntimeError as exc:
            raise RuntimeError(
                "sandbox-restricted network missing — refusing to start. "
                "Run infra/scripts/setup-sandbox-networks.sh inside DinD; "
                "see docs/architecture.md § Docker Network Segmentation. "
                f"(inspect error: {exc})"
            ) from exc

        opts = restricted.get("Options") or {}
        masq = opts.get("com.docker.network.bridge.enable_ip_masquerade")
        if masq != "false":
            raise RuntimeError(
                "sandbox-restricted exists but enable_ip_masquerade is "
                f"{masq!r} (expected 'false'). Sandboxes would have direct "
                "internet egress. Delete the network and re-run "
                "infra/scripts/setup-sandbox-networks.sh."
            )

        try:
            await self.inspect_network("sandbox-internet")
        except RuntimeError as exc:
            raise RuntimeError(
                "sandbox-internet network missing — refusing to start. "
                "Run infra/scripts/setup-sandbox-networks.sh inside DinD."
            ) from exc

        logger.info("sandbox_networks_verified")

    async def run_cmd(
        self,
        cmd: list[str],
        timeout: int = 60,
        *,
        log_context: dict[str, Any] | None = None,
        expected_failure: bool = False,
        stdin_data: bytes | None = None,
        max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        keep_tail: bool = False,
    ) -> str:
        """Execute a Docker command and return stdout.

        Raises RuntimeError on non-zero exit. Raises TimeoutError on timeout
        (and kills the subprocess to prevent zombie Docker commands that would
        otherwise block the Docker daemon).

        ``max_bytes`` bounds the stdout the command may print. The pipe is read
        in chunks (:func:`collect_output`), so whatever a process in a sandbox
        writes into an exec's output, the orchestrator holds at most that much:
        past the cap the command is stopped and :class:`OutputTooLargeError` (a
        RuntimeError) raised. ``keep_tail`` is for a command whose output is
        only a log (an install, a build, a seed script): the oldest bytes are
        dropped instead of stopping it, and the result is the output's last
        ``max_bytes``; such a command is bounded by its timeout. stderr keeps
        its first 64 KiB and its last 16 KiB. A cap above
        ``MAX_OUTPUT_CAP_BYTES`` is refused.

        stdout is decoded with ``errors="replace"``: a log read with
        ``tail -c`` can start inside a multi-byte character (#394). Only bytes
        that are not UTF-8 change, each to U+FFFD, so markers, JSON structure
        and ASCII fields decode as they always did.

        ``log_context`` is merged into failure logs only (timeout, kill, cap,
        non-zero exit). Callers that already know which container and phase a
        command belongs to — proxy cleanup, which has no job to bind a trace id
        from — pass that here. It is not a retry policy; ``run_cmd`` still fails
        immediately.

        ``expected_failure`` logs a non-zero exit at debug instead of error, for
        probes whose "no" is an answer rather than a fault (``image inspect`` of
        an image the daemon has not pulled yet, #655). It still raises.

        ``stdin_data`` is written to the command's stdin (a ``docker exec -i``):
        the way to hand a sandbox a secret without putting it on a command line.
        """
        _check_cap(max_bytes)
        scrubbed = _scrub_cmd_for_log(cmd)
        # Standard fields win over caller context so a context key cannot hide
        # the command, the daemon's stderr, or the exit code.
        context: dict[str, Any] = dict(log_context or {})
        logger.debug("docker_cmd", cmd=scrubbed)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await collect_output(
                proc,
                timeout=timeout,
                max_bytes=max_bytes,
                keep_tail=keep_tail,
                stdin_data=stdin_data,
                stuck_event="docker_cmd_kill_stuck",
                log_fields={**context, "cmd": scrubbed},
            )
        except OutputTooLargeError:
            logger.warning("docker_cmd_too_large", **{**context, "cmd": scrubbed, "max_bytes": max_bytes})
            raise
        except TimeoutError:
            # collect_output killed and reaped the subprocess, which would
            # otherwise keep running and hold a Docker slot.
            logger.warning("docker_cmd_timeout", **{**context, "cmd": scrubbed, "timeout": timeout})
            # A bare TimeoutError has an empty str() — every caller folding
            # {exc} into a failure_summary then reports "failed: " with no
            # cause (observed: "sandbox clone failed: " on job-06f63d7ba031).
            raise TimeoutError(f"docker command timed out after {timeout}s") from None

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            if not err:
                # Commands that fold stderr into stdout (`... 2>&1`) fail with
                # an empty stderr — surface the stdout tail instead, or the
                # error reads "Docker command failed: " with no cause
                # (RAV-1400: the first CC image bake failed exactly this way).
                tail = stdout.decode(errors="replace").strip()[-2000:]
                err = f"(stderr empty; stdout tail) {tail}" if tail else f"exit code {proc.returncode}"
            failure: dict[str, Any] = dict(context)
            failure.update(cmd=scrubbed, stderr=err[:2000], returncode=proc.returncode)
            if expected_failure:
                logger.debug("docker_cmd_failed", **failure)
            else:
                logger.error("docker_cmd_failed", **failure)
            raise RuntimeError(f"Docker command failed: {err}")

        return stdout.decode(errors="replace").strip()

    async def run_cmd_bytes(
        self, cmd: list[str], timeout: int = 60, *, max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    ) -> bytes:
        """Execute a Docker command and return its stdout as raw bytes, never buffering past ``max_bytes``.

        Like :meth:`run_cmd` but it does not decode or strip — for output that
        is a file's contents or a NUL-delimited listing
        (``sandbox_files.read_regular_file``, the deliverables listing). The
        same reader (:func:`collect_output`) never asks the pipe for more than
        ``max_bytes + 1`` bytes in total, so whatever a process in the sandbox
        writes into it, the orchestrator holds at most that much: past the cap
        the command is killed and :class:`OutputTooLargeError` raised. stderr
        keeps its first 64 KiB and its last 16 KiB. Raises RuntimeError on
        non-zero exit, TimeoutError on timeout.
        """
        _check_cap(max_bytes)
        scrubbed = _scrub_cmd_for_log(cmd)
        logger.debug("docker_cmd_bytes", cmd=scrubbed, max_bytes=max_bytes)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await collect_output(
                proc,
                timeout=timeout,
                max_bytes=max_bytes,
                stuck_event="docker_cmd_bytes_kill_stuck",
                log_fields={"cmd": scrubbed},
            )
        except OutputTooLargeError:
            logger.warning("docker_cmd_bytes_too_large", cmd=scrubbed, max_bytes=max_bytes)
            raise
        except TimeoutError:
            logger.warning("docker_cmd_bytes_timeout", cmd=scrubbed, timeout=timeout)
            raise TimeoutError(f"docker command timed out after {timeout}s") from None
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}"
            logger.error("docker_cmd_bytes_failed", cmd=scrubbed, stderr=err[:2000], returncode=proc.returncode)
            raise RuntimeError(f"Docker command failed: {err}")
        return stdout

    async def stream_exec(
        self,
        container: str,
        command: list[str],
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 300,
        stdin_data: bytes | None = None,
    ) -> asyncio.subprocess.Process:
        """Start a `docker exec` and return the process for streaming stdout.

        ``stdin_data`` (#304) is written to the command's stdin and the pipe
        closed before returning — the transport for payloads too large for
        argv (one argv string is capped at 128 KiB). The command must read
        stdin to EOF before it writes much to stdout/stderr, which nothing
        drains until the caller starts streaming.
        """
        cmd = self.build_exec_cmd(container, command, workdir=workdir, env=env, interactive=stdin_data is not None)
        logger.debug("docker_stream_exec", cmd=_scrub_cmd_for_log(cmd), stdin_bytes=len(stdin_data or b""))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LINE_LIMIT,
        )
        if stdin_data is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_data)
                await asyncio.wait_for(proc.stdin.drain(), timeout=_STDIN_WRITE_TIMEOUT_S)
            except (BrokenPipeError, ConnectionResetError):
                # The command exited before reading its input. Hand the
                # process back anyway: its exit code and stderr say why.
                logger.warning("docker_stream_exec_stdin_closed_early", container=container)
            except TimeoutError:
                # Nothing is reading: the caller's watchdogs only start once
                # this returns, so an unbounded drain would hang the job.
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except TimeoutError:
                    logger.error("docker_stream_exec_kill_stuck", container=container, pid=proc.pid)
                raise ExecStdinTimeoutError(
                    f"Docker exec did not read its {len(stdin_data)}-byte stdin within {_STDIN_WRITE_TIMEOUT_S:.0f}s"
                ) from None
            finally:
                proc.stdin.close()
        return proc

    async def exec_with_stdin(
        self,
        container: str,
        command: list[str],
        stdin_bytes_path: str,
        timeout: int = 300,
    ) -> None:
        """Run `docker exec -i` feeding a host-side file to the command's stdin.

        Sibling of stream_exec for the write direction — used to stream tar
        archives into a sandbox (local workspace-init contexts) without a
        host bind mount. Raises RuntimeError on non-zero exit. Its stdout is
        not used: it is read and dropped, past the last 64 KiB.
        """
        cmd = self.build_exec_cmd(container, command, interactive=True)
        logger.debug("docker_exec_with_stdin", cmd=_scrub_cmd_for_log(cmd), stdin=stdin_bytes_path)
        with open(stdin_bytes_path, "rb") as stdin_file:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=stdin_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # its own group: a timeout kills all of it (#334)
            )
            try:
                _stdout, stderr = await collect_output(
                    proc,
                    timeout=timeout,
                    max_bytes=LOG_TAIL_BYTES,
                    keep_tail=True,
                    kill_group=True,
                    stuck_event="docker_exec_with_stdin_kill_stuck",
                    log_fields={"container": container},
                )
            except TimeoutError:
                raise RuntimeError(f"Docker exec-with-stdin timed out after {timeout}s")
        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            logger.error("docker_cmd_failed", cmd=_scrub_cmd_for_log(cmd), stderr=err, returncode=proc.returncode)
            raise RuntimeError(f"Docker command failed: {err}")

    async def commit_container(self, container_id: str, image_tag: str) -> str:
        """Commit a running container as a new image; returns the tag.

        Wraps ``docker commit <container_id> <image_tag>``. Used by the
        image builder (embry0/cache/image_builder.py) to bake a
        pre-installed sandbox into a reusable image layer.
        """
        cmd = self._build_base_cmd() + ["commit", container_id, image_tag]
        await self.run_cmd(cmd, timeout=120)
        return image_tag

    # Moving bytes across the sandbox boundary goes through
    # embry0.execution.sandbox_files, never ``docker cp``: the daemon copies as
    # root and follows a symlink at the path, so a copy-in could overwrite a
    # root file and a copy-out could read one. See that module and its guard
    # test (tests/unit/execution/test_sandbox_files_guard.py).

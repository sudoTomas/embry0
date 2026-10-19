"""``DockerRuntime``, ``build_runtime`` and how the SandboxManager holds a runtime (#268)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.config import Embry0Config
from embry0.execution.docker_client import DockerClient
from embry0.execution.runtime import AgentRuntime, DockerRuntime, build_runtime
from embry0.execution.sandbox_manager import SandboxManager


def test_docker_runtime_satisfies_the_protocol() -> None:
    runtime = DockerRuntime(DockerClient())
    assert isinstance(runtime, AgentRuntime)
    assert runtime.kind == "docker"


def test_build_runtime_drives_the_configured_daemon() -> None:
    config = Embry0Config(
        _env_file=None,
        docker_host="tcp://dind:2376",
        docker_tls_verify=True,
        docker_cert_path="/certs/client",
    )
    runtime = build_runtime(config)
    assert isinstance(runtime, DockerRuntime)
    assert runtime.client._build_base_cmd() == [  # noqa: SLF001 — pins the daemon the client talks to
        "docker",
        "--host",
        "tcp://dind:2376",
        "--tlsverify",
        "--tlscacert=/certs/client/ca.pem",
        "--tlscert=/certs/client/cert.pem",
        "--tlskey=/certs/client/key.pem",
    ]


@pytest.mark.asyncio
async def test_verify_networks_is_the_clients_network_assertion() -> None:
    client = MagicMock(spec=DockerClient)
    client.assert_sandbox_networks_or_die = AsyncMock(side_effect=RuntimeError("sandbox-restricted network missing"))
    with pytest.raises(RuntimeError, match="sandbox-restricted network missing"):
        await DockerRuntime(client).verify_networks()


def test_sandbox_manager_keeps_the_runtime_it_is_given() -> None:
    client = MagicMock(spec=DockerClient)
    runtime = DockerRuntime(client)
    manager = SandboxManager(client, proxy_manager=MagicMock(), runtime=runtime)
    assert manager.runtime is runtime


def test_sandbox_manager_without_a_runtime_wraps_its_client() -> None:
    client = MagicMock(spec=DockerClient)
    manager = SandboxManager(client, proxy_manager=MagicMock())
    assert isinstance(manager.runtime, DockerRuntime)
    assert manager.runtime.client is client


# --- the Docker commands behind each operation ------------------------------


def _client(*outputs: object) -> MagicMock:
    client = MagicMock(spec=DockerClient)
    client._build_base_cmd = MagicMock(return_value=["docker", "--host", "tcp://dind:2376"])
    client.run_cmd = AsyncMock(side_effect=list(outputs) or None, return_value="")
    return client


def _issued(client: MagicMock) -> list[tuple[list[str], int | None]]:
    return [(c.args[0], c.kwargs.get("timeout")) for c in client.run_cmd.await_args_list]


@pytest.mark.asyncio
async def test_list_containers_parses_every_field() -> None:
    client = _client(
        "abc123\tsandbox-job-1\t2026-10-08 07:05:11 +0000 UTC\tUp 2 hours\t2 hours ago\n"
        "def456\tsandbox-job-2\t2026-10-08 09:00:00 -0400 EDT\tExited (0) 5 minutes ago\t5 minutes ago\n"
        "\n"
    )
    found = await DockerRuntime(client).list_containers(name="sandbox-", timeout=15)
    assert [(c.id, c.name, c.created_at, c.status, c.running_for) for c in found] == [
        ("abc123", "sandbox-job-1", "2026-10-08 07:05:11 +0000 UTC", "Up 2 hours", "2 hours ago"),
        ("def456", "sandbox-job-2", "2026-10-08 09:00:00 -0400 EDT", "Exited (0) 5 minutes ago", "5 minutes ago"),
    ]
    assert _issued(client) == [
        (
            [
                "docker", "--host", "tcp://dind:2376", "ps", "-a", "--filter", "name=sandbox-",
                "--format", "{{.ID}}\t{{.Names}}\t{{.CreatedAt}}\t{{.Status}}\t{{.RunningFor}}",
            ],
            15,
        )
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_list_containers_by_label_running_only() -> None:
    client = _client("")
    assert await DockerRuntime(client).list_containers(label="embry0.qa_job_id=", include_stopped=False) == []
    ((cmd, _),) = _issued(client)
    assert cmd[3:6] == ["ps", "--filter", "label=embry0.qa_job_id="]
    assert "-a" not in cmd


@pytest.mark.asyncio
async def test_container_and_network_commands() -> None:
    client = _client(" 2020-01-01T00:00:00Z\n", "", "net1\nnet2\n", "")
    runtime = DockerRuntime(client)
    assert await runtime.container_created("cid") == "2020-01-01T00:00:00Z"
    await runtime.remove_containers(["a", "b"])
    assert await runtime.list_networks(name="^qa-net-") == ["net1", "net2"]
    await runtime.remove_network("qa-net-1", timeout=15)
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "inspect", "cid", "--format", "{{.Created}}"], 10),
        ([*base, "rm", "-f", "a", "b"], 15),
        ([*base, "network", "ls", "-q", "--filter", "name=^qa-net-"], 10),
        ([*base, "network", "rm", "qa-net-1"], 15),
    ]


@pytest.mark.asyncio
async def test_chown_volume_runs_a_capability_free_helper_but_chown() -> None:
    client = _client("")
    await DockerRuntime(client).chown_volume("vol", "/home/agent/.m2", image="img:1", owner="agent:agent")
    assert _issued(client) == [
        (
            [
                "docker", "--host", "tcp://dind:2376", "run", "--rm", "--network=none", "--cap-drop=ALL",
                "--cap-add=CHOWN", "--security-opt=no-new-privileges", "--user", "root",
                "-v", "vol:/home/agent/.m2", "--entrypoint", "chown", "img:1", "agent:agent", "/home/agent/.m2",
            ],
            30,
        )
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_image_commands() -> None:
    client = _client("", RuntimeError("No such image"), " abc \n", "", "", "")
    runtime = DockerRuntime(client)
    assert await runtime.image_exists("img:1") is True
    assert await runtime.image_exists("img:2", timeout=10) is False
    assert await runtime.image_label("img:1", "embry0.build-hash") == "abc"
    await runtime.build_image("img:1", dockerfile="/app/D", context="/app", labels={"embry0.build-hash": "abc"})
    await runtime.build_image("img:2", dockerfile="/tmp/x.dockerfile", context=".")
    await runtime.remove_image("img:1")
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "image", "inspect", "img:1"], 60),
        ([*base, "image", "inspect", "img:2"], 10),
        ([*base, "inspect", "--format", '{{index .Config.Labels "embry0.build-hash"}}', "img:1"], 10),
        ([*base, "build", "-t", "img:1", "--label", "embry0.build-hash=abc", "-f", "/app/D", "/app"], 300),
        ([*base, "build", "-t", "img:2", "-f", "/tmp/x.dockerfile", "."], 300),
        ([*base, "rmi", "-f", "img:1"], 30),
    ]


@pytest.mark.asyncio
async def test_image_exists_lets_a_timeout_through() -> None:
    client = _client(TimeoutError("docker command timed out after 10s"))
    with pytest.raises(TimeoutError):
        await DockerRuntime(client).image_exists("img:1", timeout=10)


@pytest.mark.asyncio
async def test_compose_log_stream_puts_the_service_after_a_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def _spawn(*args: str, **kwargs: object) -> MagicMock:
        spawned.append((args, kwargs))
        return MagicMock()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
    await DockerRuntime(_client()).compose_log_stream("qa_JOB1", "web")
    ((args, kwargs),) = spawned
    assert list(args) == [
        "docker", "--host", "tcp://dind:2376", "compose", "-p", "qa_JOB1", "logs", "-f", "--tail", "200", "--", "web",
    ]  # fmt: skip
    assert kwargs["stderr"] is asyncio.subprocess.STDOUT


def _shell_client(script: str) -> MagicMock:
    """A client whose docker prefix is a real shell script (the compose args land in $0...)."""
    client = MagicMock(spec=DockerClient)
    client._build_base_cmd = MagicMock(return_value=["sh", "-c", script])
    return client


@pytest.mark.asyncio
async def test_compose_services_lists_non_empty_lines() -> None:
    assert await DockerRuntime(_shell_client("printf 'web\\n\\n db \\n'")).compose_services("qa_JOB1") == ["web", "db"]


@pytest.mark.asyncio
async def test_compose_services_raises_when_the_project_cannot_be_queried() -> None:
    with pytest.raises(RuntimeError, match="exited 1"):
        await DockerRuntime(_shell_client("exit 1")).compose_services("qa_JOB1")


@pytest.mark.asyncio
async def test_compose_services_kills_a_hung_query() -> None:
    with pytest.raises(TimeoutError):
        await DockerRuntime(_shell_client("sleep 5")).compose_services("qa_JOB1", timeout=0.2)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_volume_commands() -> None:
    inspected = (
        '[{"Name": "v1", "Labels": {"embry0.job_id": "job-1"}, "CreatedAt": "2026-07-19T00:14:06Z"},'
        ' {"Name": "v2", "Labels": null}]'
    )
    client = _client("v1\nv2\n", "v3\n", inspected, "", "", "")
    runtime = DockerRuntime(client)
    assert await runtime.list_volumes(name="embry0-qa-vol-job-") == ["v1", "v2"]
    assert await runtime.list_volumes(labels=["embry0.job_id=job-1", "embry0.instance=abc"], timeout=15) == ["v3"]
    assert [(v.name, v.labels, v.created_at) for v in await runtime.inspect_volumes(["v1", "v2"])] == [
        ("v1", {"embry0.job_id": "job-1"}, "2026-07-19T00:14:06Z"),
        ("v2", {}, None),
    ]
    await runtime.create_volume("v4", labels={"embry0.scope": "per-job", "embry0.job_id": "job-4"})
    await runtime.remove_volumes(["v1", "v2"])
    await runtime.remove_volumes(["v4"], force=True, timeout=10)
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "volume", "ls", "--quiet", "--filter", "name=embry0-qa-vol-job-"], 10),
        (
            [*base, "volume", "ls", "--quiet", "--filter", "label=embry0.job_id=job-1",
             "--filter", "label=embry0.instance=abc"],
            15,
        ),
        ([*base, "volume", "inspect", "v1", "v2"], 30),
        (
            [*base, "volume", "create", "--label", "embry0.scope=per-job", "--label", "embry0.job_id=job-4", "v4"],
            10,
        ),
        ([*base, "volume", "rm", "v1", "v2"], 15),
        ([*base, "volume", "rm", "--force", "v4"], 10),
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_containers_mounting_a_volume() -> None:
    client = _client("")
    await DockerRuntime(client).list_containers(volume="v1")
    ((cmd, _),) = _issued(client)
    assert cmd[3:7] == ["ps", "-a", "--filter", "volume=v1"]


@pytest.mark.asyncio
async def test_qa_network_commands() -> None:
    client = _client("", "", "")
    runtime = DockerRuntime(client)
    await runtime.create_network("qa-net-JOB1", labels={"embry0.qa_job_id": "JOB1"})
    await runtime.create_network("qa-net-JOB1__web", timeout=10)
    await runtime.connect_network("qa-net-JOB1", "cid")
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "network", "create", "--label", "embry0.qa_job_id=JOB1", "qa-net-JOB1"], 15),
        ([*base, "network", "create", "qa-net-JOB1__web"], 10),
        ([*base, "network", "connect", "qa-net-JOB1", "cid"], 10),
    ]


@pytest.mark.asyncio
async def test_registry_commands() -> None:
    repo_digest = "registry:5000/embry0-sandbox-qa@sha256:" + "e" * 64
    local_id = "sha256:" + "b" * 64
    client = _client(
        "",
        f"{repo_digest},|{local_id}\n",
        f"registry:5000/embry0-sandbox-qa:latest|{local_id}\n",
        "",
        "latest: digest: sha256:" + "d" * 64 + " size: 1234",
    )
    runtime = DockerRuntime(client)
    await runtime.pull_image("registry:5000/embry0-sandbox-qa:latest")
    assert await runtime.image_digests(local_id) == ([repo_digest], local_id)
    assert await runtime.container_image("cid") == ("registry:5000/embry0-sandbox-qa:latest", local_id)
    assert "digest: sha256:" in await runtime.push_image("embry0-qa-org_r1:t", "registry:5000/embry0-qa-org_r1:t")
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "pull", "--quiet", "registry:5000/embry0-sandbox-qa:latest"], 180),
        ([*base, "image", "inspect", "--format", "{{range .RepoDigests}}{{.}},{{end}}|{{.Id}}", local_id], 10),
        ([*base, "inspect", "--format", "{{.Config.Image}}|{{.Image}}", "cid"], 10),
        ([*base, "tag", "embry0-qa-org_r1:t", "registry:5000/embry0-qa-org_r1:t"], 60),
        ([*base, "push", "registry:5000/embry0-qa-org_r1:t"], 600),
    ]


@pytest.mark.asyncio
async def test_image_digests_of_a_local_only_image() -> None:
    local_id = "sha256:" + "b" * 64
    assert await DockerRuntime(_client(f"|{local_id}")).image_digests("img:1") == ([], local_id)


# --- network policy -----------------------------------------------------------


def _enforcer(monkeypatch: pytest.MonkeyPatch, addresses: dict[str, object]) -> MagicMock:
    from embry0.execution.runtime import egress

    async def container_ip(container: str, network: str) -> str | None:
        assert network == "sandbox-internet"
        found = addresses.get(container)
        if isinstance(found, Exception):
            raise found
        return found  # type: ignore[return-value]

    fake = MagicMock()
    fake.container_ip = AsyncMock(side_effect=container_ip)
    fake.ensure_helper_image = AsyncMock()
    fake.apply = AsyncMock()
    fake.clear = AsyncMock()
    monkeypatch.setattr(egress, "EgressEnforcer", MagicMock(return_value=fake))
    return fake


@pytest.mark.asyncio
async def test_attach_policy_allows_the_declared_targets_and_the_readable_proxies(monkeypatch) -> None:
    fake = _enforcer(
        monkeypatch,
        {"sbx": "172.20.0.9", "github-proxy": "172.20.0.2", "xai-proxy": None, "gone-proxy": RuntimeError("gone")},
    )
    await DockerRuntime(_client()).attach_policy(
        "sbx",
        allowed_destinations=["10.0.0.5"],
        reachable_containers=["github-proxy", "xai-proxy", "gone-proxy", "github-proxy"],
    )
    fake.ensure_helper_image.assert_awaited_once()
    fake.apply.assert_awaited_once_with("172.20.0.9", ["10.0.0.5"], proxy_ips=["172.20.0.2"])


@pytest.mark.asyncio
async def test_attach_policy_refuses_a_sandbox_without_an_internet_address(monkeypatch) -> None:
    fake = _enforcer(monkeypatch, {"sbx": None})
    with pytest.raises(RuntimeError, match="no sandbox-internet IP"):
        await DockerRuntime(_client()).attach_policy("sbx", allowed_destinations=[], reachable_containers=[])
    fake.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_detach_policy_clears_by_address_and_ignores_a_gone_container(monkeypatch) -> None:
    fake = _enforcer(monkeypatch, {"sbx": "172.20.0.9", "gone": RuntimeError("No such container")})
    runtime = DockerRuntime(_client())
    await runtime.detach_policy("sbx")
    fake.clear.assert_awaited_once_with("172.20.0.9")
    await runtime.detach_policy("gone")
    assert fake.clear.await_count == 1


@pytest.mark.asyncio
async def test_ensure_isolation_is_the_isolation_chain_on_the_runtimes_daemon(monkeypatch) -> None:
    from embry0.execution.runtime import isolation

    client = _client()
    chain = MagicMock()
    chain.ensure = AsyncMock(return_value=["172.18.0.2"])
    built = MagicMock(return_value=chain)
    monkeypatch.setattr(isolation, "SandboxIsolation", built)
    assert await DockerRuntime(client).ensure_isolation(["git-proxy"]) == ["172.18.0.2"]
    built.assert_called_once_with(client)
    chain.ensure.assert_awaited_once_with(["git-proxy"])


@pytest.mark.asyncio
async def test_sandbox_manager_enforces_policy_through_its_runtime() -> None:
    """The manager's own client is not where policy goes: the runtime is."""
    client = MagicMock(spec=DockerClient)
    runtime = MagicMock()
    runtime.ensure_isolation = AsyncMock(return_value=["172.18.0.2"])
    runtime.detach_policy = AsyncMock()
    proxy = MagicMock()
    proxy.launched_proxies = MagicMock(return_value=["git-proxy"])
    proxy.unenroll_sandbox = AsyncMock()
    client.build_stop_cmd = MagicMock(return_value=["docker", "stop"])
    client.build_rm_cmd = MagicMock(return_value=["docker", "rm"])
    client.run_cmd = AsyncMock(return_value="")
    manager = SandboxManager(client, proxy_manager=proxy, runtime=runtime)
    assert await manager.ensure_isolation() == ["172.18.0.2"]
    runtime.ensure_isolation.assert_awaited_once_with(["git-proxy"])
    await manager.destroy("sbx")
    runtime.detach_policy.assert_awaited_once_with("sbx")


# --- QA image retention (ADR 0017) ------------------------------------------------


@pytest.mark.asyncio
async def test_list_images_inspects_each_image_once() -> None:
    import json

    listed = "sha256:aaa\nsha256:aaa\nsha256:bbb\n"  # one line per tag
    inspected = [
        {
            "Id": "sha256:aaa",
            "RepoTags": ["embry0-qa-x:t1", "registry:5000/embry0-qa-x:t1"],
            "RepoDigests": ["registry:5000/embry0-qa-x@sha256:d1"],
            "Created": "2026-08-12T22:32:22.775519053Z",
            "Size": 4293400006,
        },
        {"Id": "sha256:bbb", "RepoTags": None, "RepoDigests": None, "Created": "2026-08-01T00:00:00Z", "Size": 5},
    ]
    client = _client(listed, json.dumps(inspected))
    images = await DockerRuntime(client).list_images()
    base = ["docker", "--host", "tcp://dind:2376"]
    assert _issued(client) == [
        ([*base, "image", "ls", "--all", "--quiet", "--no-trunc"], 60),
        ([*base, "image", "inspect", "sha256:aaa", "sha256:bbb"], 60),
    ]
    assert images[0].repo_tags == ("embry0-qa-x:t1", "registry:5000/embry0-qa-x:t1")
    assert images[0].repo_digests == ("registry:5000/embry0-qa-x@sha256:d1",)
    assert (images[0].size, images[1].repo_tags, images[1].repo_digests) == (4293400006, (), ())


@pytest.mark.asyncio
async def test_image_refs_are_removed_without_force_and_holders_found_by_ancestor() -> None:
    client = _client("", "")
    runtime = DockerRuntime(client)
    await runtime.remove_image_refs(["embry0-qa-x:t1", "registry:5000/embry0-qa-x@sha256:d1"])
    await runtime.list_containers(ancestor="sha256:aaa")
    (rmi, _), (ps, _) = _issued(client)
    assert rmi[3:] == ["rmi", "embry0-qa-x:t1", "registry:5000/embry0-qa-x@sha256:d1"], "rmi must not carry -f"
    assert ps[3:7] == ["ps", "-a", "--filter", "ancestor=sha256:aaa"]


@pytest.mark.asyncio
async def test_disk_usage_reads_unique_image_sizes_and_volume_sizes() -> None:
    import json

    from embry0.execution.runtime.docker import parse_size

    df = {
        "Images": [
            {"ID": "sha256:aaa", "UniqueSize": "400.7MB", "Size": "1.38GB"},
            {"ID": "sha256:bbb", "UniqueSize": "N/A"},
        ],
        "Volumes": [{"Name": "embry0-qa-vol-x", "Size": "158.3MB"}],
    }
    client = _client(json.dumps(df))
    usage = await DockerRuntime(client).disk_usage()
    ((cmd, timeout),) = _issued(client)
    assert cmd[3:] == ["system", "df", "-v", "--format", "{{json .}}"] and timeout == 120
    assert usage.image_unique_bytes == {"sha256:aaa": 400_700_000}
    assert usage.volume_bytes == {"embry0-qa-vol-x": 158_300_000}
    assert (parse_size("0B"), parse_size("12kB"), parse_size("1.5GB"), parse_size("")) == (
        0,
        12_000,
        1_500_000_000,
        None,
    )

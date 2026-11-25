"""A background call to the operator's own server holds one of its slots (#174).

The briefs and the Slop-O-Meter's judgments call a local model outside any job. Each such call takes a slot
of the server it reaches, as an agent run does, for the length of the call; with every slot taken it is not
made, and the feature's tick stops there. A hosted model's call takes none.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from embry0.services.background_models import BackgroundCallRefused, BackgroundCalls, resolve_endpoint
from embry0.services.local_slots import LocalSlots

try:
    from embry0.services.local_slots import ENV_SERVER, CallSlots
except ImportError:
    pytest.fail("a background call holds no slot: services.local_slots names no server and no CallSlots", pytrace=False)
from embry0.services.proposals import Proposal
from tests.fixtures.leads import brief_reader
from tests.fixtures.provider_catalogue import enable_providers
from tests.unit.services.test_background_calls import GOOD, T0, Gate, Spend

LOCAL = "custom/qwen"


@pytest.fixture(autouse=True)
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_providers(monkeypatch, "openai", "custom")


class Resolver:
    def __init__(self, credential_id: str | None = "cred-local", base_url: str = "http://models.test:8000/v1") -> None:
        self.credential_id = credential_id
        self.base_url = base_url

    async def resolve_llm(self, repo: str, provider: str) -> Any:
        return SimpleNamespace(id=self.credential_id, secret="k", metadata={"base_url": self.base_url})


class Leases:
    """The lease store: ``free`` slots, every take and every release."""

    def __init__(self, free: int = 1) -> None:
        self.free = free
        self.taken: list[dict[str, Any]] = []
        self.released: list[str] = []

    async def try_take(self, **kwargs: Any) -> str | None:
        if len(self.taken) - len(self.released) >= self.free:
            return None
        self.taken.append(kwargs)
        return f"lease-{len(self.taken)}"

    async def release(self, lease_id: str) -> None:
        self.released.append(lease_id)


def _calls(leases: Leases, *, cap: Any = 1, gate: Gate | None = None, spend: Spend | None = None) -> BackgroundCalls:
    async def read_cap() -> int:
        if isinstance(cap, Exception):
            raise cap
        return int(cap)

    return BackgroundCalls(
        resolver=Resolver(),
        spend=spend or Spend(),
        gate=gate or Gate(),
        local_slots=CallSlots(slots=LocalSlots(leases), cap=read_cap),
    )


def _answering(sent: list[dict[str, Any]], leases: Leases | None = None, *, fail: bool = False) -> httpx.AsyncClient:
    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(
            {"body": json.loads(request.content), "held": len(leases.taken) - len(leases.released)} if leases else {}
        )
        if fail:
            raise httpx.ConnectError("the server went away")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}], "usage": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


async def _ask(calls: BackgroundCalls, client: httpx.AsyncClient, feature: str = "slop_judge") -> str:
    endpoint = await calls.endpoint(feature, LOCAL, "acme/widgets")
    assert endpoint is not None and endpoint.local
    return await calls.ask(feature, endpoint, client, repo="acme/widgets", system="s", user="u", schema={}, name="j")


async def test_a_local_call_holds_a_slot_of_its_credential_s_server_while_it_is_made() -> None:
    leases, sent = Leases(), []
    calls = _calls(leases, cap=3)
    async with _answering(sent, leases) as client:
        assert json.loads(await _ask(calls, client)) == GOOD
    assert [s["held"] for s in sent] == [1], "the slot is held while the server answers"
    [taken] = leases.taken
    assert (taken["server"], taken["cap"], taken["agent_type"], taken["model"], taken["job_id"]) == (
        "cred-local",
        3,
        "slop_judge",
        LOCAL,
        "",
    ), f"the slot is the credential's server's, under the cap read now: {taken}"
    assert leases.released == ["lease-1"], "and given back once the answer is in"


async def test_a_call_that_fails_gives_its_slot_back() -> None:
    leases, sent = Leases(), []
    async with _answering(sent, leases, fail=True) as client:
        with pytest.raises(httpx.ConnectError):
            await _ask(_calls(leases), client)
    assert leases.released == ["lease-1"], "a failed call never keeps the server's slot"


async def test_with_every_slot_taken_nothing_is_sent_and_it_is_a_refusal() -> None:
    leases, sent, spend = Leases(free=0), [], Spend()
    async with _answering(sent, leases) as client:
        with pytest.raises(BackgroundCallRefused) as busy:
            await _ask(_calls(leases, spend=spend), client)
    assert sent == [] and spend.rows == [], "nothing is sent and nothing is recorded"
    assert busy.value.decision.limit == "local_slots"
    assert "slot" in str(busy.value) and "budget" not in str(busy.value), str(busy.value)


async def test_a_cap_that_cannot_be_read_is_no_slot() -> None:
    leases, sent = Leases(), []
    async with _answering(sent, leases) as client:
        with pytest.raises(BackgroundCallRefused):
            await _ask(_calls(leases, cap=RuntimeError("database down")), client)
    assert sent == [] and leases.taken == []


async def test_a_hosted_model_s_call_takes_no_slot() -> None:
    leases, sent = Leases(free=0), []
    calls = _calls(leases)
    endpoint = await calls.endpoint("job_brief", "gpt-6-luna", "acme/widgets")
    assert endpoint is not None and not endpoint.local
    async with _answering(sent, leases) as client:
        await calls.ask("job_brief", endpoint, client, repo=None, system="s", user="u", schema={}, name="b")
    assert len(sent) == 1 and leases.taken == []


async def test_a_brief_writer_stops_its_tick_on_a_busy_server_and_writes_once_a_slot_is_free() -> None:
    from embry0.services.proposal_briefs import ProposalBriefWriter

    leases, sent, rows = Leases(free=0), [], {}

    class Briefs:
        async def all(self) -> dict[str, Any]:
            return dict(rows)

        async def save(self, pid: str, digest: str, brief: Any, *, model: str, prompt_hash: str = "") -> None:
            rows[pid] = {"input_hash": digest, "model": model}

        async def prune(self, keep: set[str]) -> int:
            return 0

    def proposal(n: int) -> Proposal:
        return Proposal(
            id=f"case:hc-{n}",
            kind="case",
            repo="acme/widgets",
            title="Stop retrying a saved email",
            summary="…",
            details="Seen 3 times",
            action="Run the fix pipeline",
            created_at=T0,
            extra={},
        )

    async def feed() -> list[Proposal]:
        return [proposal(1), proposal(2)]

    async def models() -> Any:
        return lambda repo: LOCAL

    writer = ProposalBriefWriter(
        proposals=feed,
        settings=brief_reader(models),
        briefs=Briefs(),
        calls=_calls(leases),
        client=_answering(sent, leases),
    )
    assert await writer.tick() == [], "every slot taken: the tick stops, and nothing is sent"
    assert sent == [] and leases.taken == []
    leases.free = 1
    assert sorted(await writer.tick()) == ["case:hc-1", "case:hc-2"], "nothing was held back for a retry delay"
    assert [s["held"] for s in sent] == [1, 1] and leases.released == ["lease-1", "lease-2"]


@pytest.mark.parametrize(
    ("credential_id", "base_url", "server"),
    [
        ("cred-local", "http://models.test:8000/v1", "cred-local"),
        (None, "http://models.test:8000/v1", ENV_SERVER),
        # The same machine by another name is the same credential's server: the credential, not the name, tells.
        ("cred-local", "http://10.0.0.5:8000/v1", "cred-local"),
    ],
)
async def test_a_local_endpoint_names_the_server_its_credential_is(
    credential_id: str | None, base_url: str, server: str
) -> None:
    """ADR 0032: a call on the custom provider holds a slot of its credential's server, as an agent run does, and
    reaches that credential's own address."""
    endpoint = await resolve_endpoint(LOCAL, Resolver(credential_id, base_url), "acme/widgets")
    assert endpoint is not None and endpoint.server == server, (
        f"a call on the custom provider must hold a slot of its credential's server: {endpoint}"
    )
    assert endpoint.root == base_url.rstrip("/"), f"a background call reaches its credential's own address: {endpoint}"

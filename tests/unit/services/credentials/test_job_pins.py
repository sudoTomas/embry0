"""Run with (spec 2026-10-25): the key a person chose for a job serves it, and nothing else does in its place."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.credentials.resolver import CredentialResolver, CredentialUnavailableError
from tests.unit.services.credentials.test_resolve_llm import LlmRepo, _row
from tests.unit.services.credentials.test_resolver import FakeSecrets, _cfg

POOL = _row("pool", "tok-pool", provider="openrouter", kind="llm_api_key")
CHOSEN = _row("chosen", "tok-chosen", provider="openrouter", kind="llm_api_key")


def _resolver(*rows, pins=None, broken=False):
    repo = LlmRepo({("global", "", "llm", "openrouter"): [POOL]})
    for row in rows:
        repo.by_id[row["id"]] = row

    async def job_pins(job_id):
        if broken:
            raise RuntimeError("db down")
        return (pins or {}).get(job_id, {})

    env = {"OPENAI_COMPATIBLE_PROVIDERS": "openrouter"}
    return repo, CredentialResolver(repo, FakeSecrets(), _cfg(), environ=env, job_pins=job_pins)


async def test_the_chosen_key_serves_the_job_over_its_bindings():
    repo, r = _resolver(CHOSEN, pins={"job-1": {"openrouter": CHOSEN["id"]}})
    got = await r.resolve_llm_for_job("acme/app", ["openrouter"], job_id="job-1")
    assert got["openrouter"].secret == "tok-chosen" and got["openrouter"].id == CHOSEN["id"]
    assert repo.uses[-1] == {
        "credential_id": CHOSEN["id"],
        "credential_label": "chosen",
        "job_id": "job-1",
        "role": "llm",
        "provider": "openrouter",
    }
    other = await r.resolve_llm("acme/app", "openrouter", job_id="job-2")
    assert other.secret == "tok-pool", "a job that chose nothing resolves as before"


async def test_the_chosen_key_outranks_a_parent_job_s():
    repo, r = _resolver(CHOSEN, pins={"child": {"openrouter": CHOSEN["id"]}})
    repo.job_uses["parent"] = [{"role": "llm", "provider": "openrouter", "credential_id": POOL["id"]}]
    got = await r.resolve_llm_for_job("acme/app", ["openrouter"], job_id="child", parent_job_id="parent")
    assert got["openrouter"].id == CHOSEN["id"]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (None, "no longer exists"),
        (dict(CHOSEN, status="disabled"), "disabled"),
        (dict(CHOSEN, status="quarantined", quarantined_until=datetime.now(UTC) + timedelta(hours=1)), "quarantined"),
        (dict(CHOSEN, secret_ciphertext="enc:BAD"), "decrypted"),
        (dict(CHOSEN, provider="xai"), "not a openrouter key"),
    ],
)
async def test_a_chosen_key_that_cannot_serve_fails_the_job_instead_of_another(row, why):
    rows = (row,) if row is not None else ()
    _, r = _resolver(*rows, pins={"job-1": {"openrouter": CHOSEN["id"]}})
    with pytest.raises(CredentialUnavailableError, match=why):
        await r.resolve_llm("acme/app", "openrouter", job_id="job-1")


async def test_pins_that_cannot_be_read_fail_the_job():
    _, r = _resolver(broken=True)
    with pytest.raises(CredentialUnavailableError, match="could not be read"):
        await r.resolve_llm("acme/app", "openrouter", job_id="job-1")
    assert (await r.resolve_llm("acme/app", "openrouter")).secret == "tok-pool", "no job, no pin to read"

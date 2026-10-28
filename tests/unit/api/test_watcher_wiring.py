"""The production watcher is built with every repository it needs (#369).

`WatcherService.__init__` takes `signatures_repo` optionally, so omitting it is
not a TypeError — it is a watcher that clusters, logs a shadow comparison every
tick, and never writes a row. `log_signatures` was empty for the whole of
#289's shadow period because of exactly that.

This is asserted against the source rather than a live app because the wiring
lives in `lifespan`, which needs Docker and a database; `_init_app_state`, the
part the `api_client` fixture runs, does not build the watcher. The AST is
read rather than the text so reformatting cannot silently defeat the check.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import embry0.api.app as app_module

#: Constructor arguments the deployed watcher cannot do its job without. Each
#: one is optional in the signature, so a missing one fails silently at
#: runtime rather than loudly at startup — which is the reason for this test.
REQUIRED_IN_PRODUCTION = (
    "config",
    "jobs_repo",
    "watcher_runs_repo",
    "integration_repo",
    "executor",
    "patterns_repo",
    "cases_repo",
    "case_recorder",
    "deploy_sync",
    # #369 — without this, clustering measures nothing.
    "signatures_repo",
    # #378 — without this the tick records an error instead of filing.
    "filer",
    # Spec 2026-10-25 — without this a stack's auto_accept setting does nothing.
    "auto_acceptor",
    # #172 — without this a mask stored after startup never reaches a tick.
    "signature_masks",
)


def _watcher_service_call() -> ast.Call:
    source = pathlib.Path(app_module.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "WatcherService":
            return node
    pytest.fail("no WatcherService(...) construction found in embry0/api/app.py")


def test_production_wiring_passes_every_repository_the_watcher_needs() -> None:
    call = _watcher_service_call()
    passed = {kw.arg for kw in call.keywords if kw.arg}
    missing = [name for name in REQUIRED_IN_PRODUCTION if name not in passed]
    assert not missing, (
        f"embry0/api/app.py builds WatcherService without {missing}. "
        "Every one of these is an optional ctor argument, so the watcher will start "
        "and quietly do less than it reports."
    )


def test_signatures_repo_is_a_log_signatures_repository() -> None:
    """Pinned separately: passing the wrong repository here would satisfy the
    test above while still writing nothing to `log_signatures`."""
    call = _watcher_service_call()
    passed = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    node = passed.get("signatures_repo")
    assert node is not None, "signatures_repo is not passed at all"
    assert isinstance(node, ast.Attribute), f"expected app.state.<attr>, got {ast.dump(node)}"

    source = pathlib.Path(app_module.__file__).read_text()
    assigned = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "LogSignaturesRepository"
    ]
    assert assigned, "app.py never constructs a LogSignaturesRepository"
    targets = {ast.unparse(t) for a in assigned for t in a.targets}
    assert ast.unparse(node) in targets, (
        f"signatures_repo={ast.unparse(node)} is not the LogSignaturesRepository built in app.py "
        f"(that one is assigned to {sorted(targets)})"
    )


# ---- #312: the confirm-healed sweep ---------------------------------------


def _confirm_sweep_call() -> ast.Call:
    source = pathlib.Path(app_module.__file__).read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ConfirmHealedSweep":
            return node
    pytest.fail("no ConfirmHealedSweep(...) construction found in embry0/api/app.py")


def test_confirm_healed_is_wired_with_the_signature_memory() -> None:
    """The sweep counts through `log_signatures`. Built without that repository
    it would read every signature as absent and call every case healed — the
    one wrong answer that closes a ticket."""
    passed = {kw.arg for kw in _confirm_sweep_call().keywords if kw.arg}
    for required in ("cases_repo", "cases_service", "signatures_repo", "config"):
        assert required in passed, f"ConfirmHealedSweep is built without {required!r}"


def test_the_filer_is_built_per_stack() -> None:
    """A watcher whose filer is None files nowhere and records an error every
    tick. Since #378 the filer follows the stack's own tracker, not the
    deployment's Linear config — build_filer decides (tested in
    tests/unit/services/test_watcher_multi_stack.py)."""
    call = _watcher_service_call()
    passed = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    assert ast.unparse(passed["filer"]).startswith("build_filer(stack")


def test_the_stack_table_is_wired_and_seeded_at_startup() -> None:
    """Without the seed an upgrading deployment starts with no stacks at all:
    the watcher would have nothing to watch until someone wrote a row."""
    source = pathlib.Path(app_module.__file__).read_text()
    tree = ast.parse(source)

    assigned = {
        ast.unparse(target)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if ast.unparse(node.value).startswith("WatcherStacksRepository(")
    }
    assert "app.state.watcher_stacks_repo" in assigned, "app.py never builds the stacks repository"

    seeded = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "seed_stacks"
    ]
    assert seeded, "app.py never seeds the env stack (services/watcher_stacks.seed_stacks)"


# ---- spec 2026-10-25: auto-accept ------------------------------------------


@pytest.mark.asyncio
async def test_the_auto_acceptor_accepts_through_the_case_route_as_its_own_actor(monkeypatch) -> None:
    """The draft is accepted by the path POST /cases/{id}/accept takes, under
    embry0:auto-accept, with an audit row of its own; the day's count is that
    actor's dispatches."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from embry0.api.v1 import cases as cases_api
    from embry0.orchestration.dispatch_actors import AUTO_ACCEPT
    from embry0.services.watcher_auto_accept import AutoAcceptBar

    now = datetime.now(UTC)
    draft = {
        "id": "hc-1",
        "origin": "watcher",
        "stage": "tracked",
        "severity": "high",
        "occurrence_count": 50,
        "issue_id": None,
        "tracked_at": now - timedelta(days=2),
    }
    accepted: list[tuple[str, str]] = []

    async def accept_case(case_id, body, request, cases):
        accepted.append((case_id, body.accepted_by))
        return {"job_id": "job-9"}

    audits: list[tuple[str, str]] = []

    async def emit_audit(db, action, *, actor, **kw):
        audits.append((action, actor))

    monkeypatch.setattr(cases_api, "accept_case", accept_case)
    monkeypatch.setattr("embry0.audit.helpers.emit_audit", emit_audit)
    db = SimpleNamespace(fetchval=AsyncMock(return_value=0))
    app = SimpleNamespace(
        state=SimpleNamespace(
            db=db,
            config=SimpleNamespace(audit_log_path=None),
            healing_cases_repo=SimpleNamespace(tracked_watcher_drafts=AsyncMock(return_value=[draft])),
            proposals_repo=SimpleNamespace(decisions=AsyncMock(return_value={})),
        )
    )

    done = await app_module._watcher_auto_acceptor(app)("owner/repo", AutoAcceptBar())
    assert done == [{"case_id": "hc-1", "job_id": "job-9"}]
    assert accepted == [("hc-1", AUTO_ACCEPT)]
    assert audits == [("case_auto_accepted", AUTO_ACCEPT)]
    assert db.fetchval.await_args.args[1] == AUTO_ACCEPT, "the day's cap counts the auto-accept actor's jobs"
    assert db.fetchval.await_args.args[4] == "owner/repo", "#172: and only this stack's repository's"


# ---- #172: a stack's signature masks, read every tick ----------------------


def test_each_watcher_reads_its_own_stacks_masks() -> None:
    """Pinned separately: another stack's reader, or one built once at startup, would
    satisfy the test above and still read the wrong masks or none after a PUT."""
    passed = {kw.arg: ast.unparse(kw.value) for kw in _watcher_service_call().keywords if kw.arg}
    assert passed.get("signature_masks") == "_watcher_signature_masks(app, stack.repo)", (
        f"each watcher must read its own stack's masks every tick, not {passed.get('signature_masks')!r}"
    )


@pytest.mark.asyncio
async def test_the_mask_reader_reads_the_stacks_row_now() -> None:
    from types import SimpleNamespace

    import structlog

    from embry0.services.watcher_stacks import WatcherStack

    reader_for = getattr(app_module, "_watcher_signature_masks", None)
    assert reader_for is not None, "app.py has no reader for a stack's signature masks"
    mask = {"pattern": r"\brec_[a-z0-9]{7}\b", "replacement": "<record>"}
    rows = {
        "acme/widgets": WatcherStack(repo="acme/widgets", logql="{x=1}", settings={"signature_masks": [mask]}),
        "acme/legacy": WatcherStack(
            repo="acme/legacy", logql="{x=1}", settings={"signature_masks": [{"pattern": "("}]}
        ),
    }

    class Stacks:
        async def get(self, repo: str) -> WatcherStack | None:
            return rows.get(repo)

    app = SimpleNamespace(state=SimpleNamespace(watcher_stacks_repo=Stacks()))
    assert await reader_for(app, "acme/widgets")() == [mask]
    assert await reader_for(app, "acme/gone")() is None, "no row is not the same as no masks"

    with structlog.testing.capture_logs() as logs:
        assert await reader_for(app, "acme/legacy")() == [], "a stored value that no longer checks reads as none"
    assert any(e["event"] == "watcher_stack_setting_invalid" and e.get("key") == "signature_masks" for e in logs)

    rows["acme/widgets"] = WatcherStack(repo="acme/widgets", logql="{x=1}")
    assert await reader_for(app, "acme/widgets")() == [], "the reader read the row once, not every call"

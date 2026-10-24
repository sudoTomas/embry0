"""Unit tests for embry0.mcp_server.server — tool registry, confirm-gates, log tailing."""

import pytest

from embry0.mcp_server import server
from embry0.services.organizations import SETTINGS

EXPECTED_TOOLS = {
    "health",
    "stats",
    "issue_list",
    "issue_get",
    "issue_create",
    "job_list",
    "job_get",
    "job_create",
    "job_wait",
    "job_resume",
    "job_inputs",
    "job_answer",
    "job_cancel",
    "job_logs",
    "job_deliverables",
    "agent_list",
    "agent_get",
    "agent_create",
    "agent_update",
    "agent_delete",
    "agent_reset",
    "template_list",
    "template_get",
    "template_validate",
    "watcher_runs",
    "case_list",
    "case_get",
    "merge_approval_list",
    "merge_approval_get",
    "merge_decide",
    "case_accept",
    "case_close",
    "logs_query",
    "watcher_trigger",
    "env_list",
    "env_set",
    "env_delete",
    "repo_list",
    "repo_get",
    "repo_register",
    "org_list",
    "org_get",
    "org_needs",
    "org_register",
    "org_update",
    "context_audit_status",
    "context_audit_run",
    "host_list",
    "host_get",
    "host_probe",
    "host_import_tailnet",
    "standard_get",
    "standard_set",
    "standard_audit",
    "org_webhook_install",
    "org_act",
    "qa_config_get",
    "qa_config_set",
    "credential_list",
    "credential_resolve",
    "credential_bind",
    "skill_list",
    "skill_source_list",
    "skill_source_add",
    "skill_source_sync",
    "skill_source_remove",
    "skill_source_get",
    "skill_source_update",
    "inline_skill_list",
    "inline_skill_put",
    "inline_skill_remove",
    "agent_skill_add",
    "agent_skill_remove",
    "tool_list",
    "proposal_list",
    "proposal_get",
    "proposal_accept",
    "proposal_decline",
    "proposal_snooze",
}

CONFIRM_GATED = {
    "merge_decide",
    "case_accept",
    "case_close",
    "job_resume",
    "job_answer",
    "job_cancel",
    "agent_create",
    "agent_update",
    "agent_delete",
    "agent_reset",
    "env_set",
    "env_delete",
    "repo_register",
    "org_register",
    "org_update",
    "context_audit_run",
    "host_import_tailnet",
    "standard_set",
    "standard_audit",
    "org_webhook_install",
    "org_act",
    "qa_config_set",
    "credential_bind",
    "skill_source_add",
    "skill_source_sync",
    "skill_source_remove",
    "skill_source_update",
    "inline_skill_put",
    "inline_skill_remove",
    "agent_skill_add",
    "agent_skill_remove",
    "proposal_accept",
    "proposal_decline",
    "proposal_snooze",
}


class RecordingClient:
    """Stands in for Embry0Client; records every method call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"called": name}

        return method


@pytest.fixture
def fake_client(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(server, "_client", client)
    return client


async def test_all_expected_tools_registered():
    tools = {tool.name for tool in await server.mcp.list_tools()}
    assert tools == EXPECTED_TOOLS


async def test_confirm_gated_tools_refuse_without_confirm(fake_client):
    refusals = [
        await server.job_resume("j1", "abandon"),
        await server.job_answer("j1", "inp-1", "use option A"),
        await server.job_cancel("j1"),
        await server.agent_create("lantern", "lights", "m"),
        await server.agent_update("developer", model="m"),
        await server.agent_delete("lantern"),
        await server.agent_reset("developer"),
        await server.env_set("K", "v"),
        await server.env_delete("K"),
        await server.repo_register("acme/widgets"),
        await server.org_register("acme"),
        await server.org_update("acme", settings={"context_audit_days": 7}),
        await server.context_audit_run("acme"),
        await server.context_audit_run("acme", repo="acme/widgets", force=True),
        await server.org_webhook_install("acme"),
        await server.org_act("acme", "register", ["acme/widgets"]),
        await server.org_act("acme", "hook", ["acme/widgets"], dry_run=True),
        await server.qa_config_set("acme/widgets", "version: 2"),
        await server.host_import_tailnet(),
        await server.standard_set("acme", "design", {"roles": {}}, {}),
        await server.standard_audit("acme", "design", "acme/widgets"),
        await server.credential_bind("acme/widgets", "cred-000000000009"),
    ]
    for refusal in refusals:
        assert refusal["error"] == "confirmation_required"
        assert "confirm=true" in refusal["message"]
    bind_refusal = refusals[-1]
    assert "acme/widgets" in bind_refusal["message"]
    assert "cred-000000000009" in bind_refusal["message"]
    assert fake_client.calls == []


async def test_confirm_gated_tools_pass_through_with_confirm(fake_client):
    assert await server.job_cancel("j1", confirm=True) == {"called": "job_cancel"}
    assert await server.job_resume("j1", "merge_as_is", confirm=True) == {"called": "job_resume"}
    assert await server.job_answer("j1", "inp-1", "use option A", confirm=True) == {"called": "job_input_answer"}
    assert await server.agent_create("lantern", "lights", "m", confirm=True) == {"called": "agent_create"}
    assert await server.agent_update("developer", model="m", confirm=True) == {"called": "agent_update"}
    assert await server.agent_delete("lantern", confirm=True) == {"called": "agent_delete"}
    assert await server.agent_reset("developer", confirm=True) == {"called": "agent_reset"}
    assert await server.env_set("K", "v", confirm=True) == {"called": "env_set"}
    assert await server.env_delete("K", confirm=True) == {"called": "env_delete"}
    assert await server.repo_register("acme/widgets", confirm=True) == {"called": "repo_register"}
    assert await server.org_register("acme", confirm=True) == {"called": "org_register"}
    assert await server.org_update("acme", settings={"context_audit_days": 7}, confirm=True) == {"called": "org_update"}
    assert await server.context_audit_run("acme", confirm=True) == {"called": "context_audit_run"}
    assert await server.org_webhook_install("acme", confirm=True) == {"called": "org_webhook_install"}
    assert await server.org_act("acme", "register", ["acme/widgets"], confirm=True) == {"called": "org_act"}
    assert await server.qa_config_set("acme/widgets", "version: 2", confirm=True) == {"called": "qa_config_set"}
    assert await server.host_import_tailnet(confirm=True) == {"called": "host_import_tailnet"}
    assert await server.standard_set("acme", "design", {"roles": {}}, {}, confirm=True) == {"called": "standard_set"}
    assert await server.standard_audit("acme", "design", "acme/widgets", confirm=True) == {"called": "standard_audit"}
    assert await server.credential_bind(
        "acme/widgets", "cred-000000000009", role="forge", pinned=True, confirm=True
    ) == {"called": "credential_bind"}
    assert fake_client.calls[-1] == (
        "credential_bind",
        ("acme/widgets", "cred-000000000009", "forge", True),
        {},
    )
    assert [name for name, _, _ in fake_client.calls] == [
        "job_cancel",
        "job_resume",
        "job_input_answer",
        "agent_create",
        "agent_update",
        "agent_delete",
        "agent_reset",
        "env_set",
        "env_delete",
        "repo_register",
        "org_register",
        "org_update",
        "context_audit_run",
        "org_webhook_install",
        "org_act",
        "qa_config_set",
        "host_import_tailnet",
        "standard_set",
        "standard_audit",
        "credential_bind",
    ]


async def test_host_tools_pass_through(fake_client):
    assert await server.host_list(source="tailnet") == {"called": "host_list"}
    assert await server.host_get("host-0123456789ab") == {"called": "host_get"}
    assert await server.host_probe("host-0123456789ab") == {"called": "host_probe"}
    assert await server.host_probe("host-0123456789ab", credential_id="cred-000000000009") == {"called": "host_probe"}
    assert await server.host_import_tailnet(credential_id="cred-000000000009", confirm=True) == {
        "called": "host_import_tailnet"
    }
    assert fake_client.calls == [
        ("host_list", (), {"source": "tailnet", "include_removed": False}),
        ("host_get", ("host-0123456789ab",), {}),
        ("host_probe", ("host-0123456789ab",), {"credential_id": ""}),
        ("host_probe", ("host-0123456789ab",), {"credential_id": "cred-000000000009"}),
        ("host_import_tailnet", (), {"credential_id": "cred-000000000009"}),
    ]


async def test_standard_tools_pass_through(fake_client):
    schema = {"roles": {"color.accent": {"type": "color"}}}
    values = {"color.accent": "#3366ff"}
    assert await server.standard_get("acme", "design") == {"called": "standard_get"}
    assert await server.standard_set("acme", "design", schema, values, prose="One accent.", confirm=True) == {
        "called": "standard_set"
    }
    assert await server.standard_set("acme", "design", schema, values, references=["acme/site"], confirm=True) == {
        "called": "standard_set"
    }
    assert await server.standard_audit("acme", "design", "acme/widgets", confirm=True) == {"called": "standard_audit"}
    assert fake_client.calls == [
        ("standard_get", ("acme", "design"), {}),
        (
            "standard_set",
            ("acme", "design"),
            {"schema": schema, "values": values, "prose": "One accent.", "references": []},
        ),
        (
            "standard_set",
            ("acme", "design"),
            {"schema": schema, "values": values, "prose": "", "references": ["acme/site"]},
        ),
        ("standard_audit", ("acme", "design", "acme/widgets"), {}),
    ]


async def test_standard_refusals_name_the_action(fake_client):
    write = await server.standard_set("acme", "design", {}, {})
    audit = await server.standard_audit("acme", "design", "acme/widgets")
    assert "standard_set" in write["message"] and "design standard of 'acme'" in write["message"]
    assert "standard_audit" in audit["message"] and "'acme/widgets'" in audit["message"]
    assert fake_client.calls == []


async def test_host_import_tailnet_refusal_names_the_action(fake_client):
    refusal = await server.host_import_tailnet(credential_id="cred-000000000009")
    assert refusal["error"] == "confirmation_required"
    assert "import hosts" in refusal["message"] and "host_import_tailnet" in refusal["message"]
    assert fake_client.calls == []


async def test_org_act_needs_confirm_even_for_a_dry_run(fake_client):
    refusal = await server.org_act("acme", "hook", ["acme/widgets"], dry_run=True)
    assert refusal["error"] == "confirmation_required"
    assert fake_client.calls == []
    assert await server.org_act("acme", "hook", ["acme/widgets"], dry_run=True, confirm=True) == {"called": "org_act"}
    assert await server.org_act("acme", "onboard", ["acme/widgets"], force=True, confirm=True) == {"called": "org_act"}
    assert fake_client.calls == [
        ("org_act", ("acme", "hook", ["acme/widgets"]), {"dry_run": True, "force": False}),
        ("org_act", ("acme", "onboard", ["acme/widgets"]), {"dry_run": False, "force": True}),
    ]


async def test_read_tools_pass_through(fake_client):
    assert await server.health() == {"called": "health"}
    assert await server.watcher_trigger() == {"called": "watcher_trigger"}
    assert await server.watcher_trigger(stack="acme/api") == {"called": "watcher_trigger"}
    assert fake_client.calls[-1][2] == {"timeout": 900.0, "stack": "acme/api"}
    assert await server.watcher_runs(stack="acme/api") == {"called": "watcher_runs"}
    assert fake_client.calls[-1][2] == {"limit": 50, "stack": "acme/api"}
    assert await server.job_wait("j1", timeout=1.0) == {"called": "job_wait"}
    name, _, kwargs = fake_client.calls[-1]
    assert name == "job_wait"
    assert kwargs == {"timeout": 1.0, "poll_interval": 5.0}


async def test_job_logs_tail_truncates(monkeypatch):
    class LogsClient:
        async def job_logs(self, job_id):
            return {"events": [{"n": i} for i in range(100)]}

    monkeypatch.setattr(server, "_client", LogsClient())
    result = await server.job_logs("j1", tail=10)
    assert len(result["events"]) == 10
    assert result["events"][-1] == {"n": 99}
    assert result["truncated"] is True
    assert result["total_events"] == 100
    full = await server.job_logs("j1", tail=0)
    assert len(full["events"]) == 100
    assert "truncated" not in full


async def test_refusal_names_the_action():
    refusal = await server.env_delete("MY_KEY", repo="acme/app")
    assert "MY_KEY" in refusal["message"]
    assert "acme/app" in refusal["message"]


async def test_repo_read_tools_pass_through(fake_client):
    assert await server.repo_list() == {"called": "repo_list"}
    assert await server.repo_get("acme/widgets") == {"called": "repo_get"}
    # qa_config_get wraps the raw yaml text in a dict for the MCP surface.
    out = await server.qa_config_get("acme/widgets")
    assert out == {"repo": "acme/widgets", "yaml_text": {"called": "qa_config_get"}}


async def test_org_read_tools_pass_through(fake_client):
    assert await server.org_list() == {"called": "org_list"}
    assert await server.org_get("acme") == {"called": "org_get"}
    assert await server.org_needs("acme") == {"called": "org_needs"}
    assert fake_client.calls == [
        ("org_list", (), {"include_removed": False}),
        ("org_get", ("acme",), {}),
        ("org_needs", ("acme",), {}),
    ]


async def test_context_audit_tools_relay_every_field(fake_client):
    assert await server.context_audit_status("acme") == {"called": "context_audit_status"}
    settings = {"context_audit_days": 7, "context_audit_budget_usd": 5, "context_audit_max_jobs_per_tick": None}
    assert await server.org_update("acme", settings=settings, confirm=True) == {"called": "org_update"}
    assert await server.org_update("acme", display_name="Acme", confirm=True) == {"called": "org_update"}
    assert await server.context_audit_run("acme", limit=2, confirm=True) == {"called": "context_audit_run"}
    assert await server.context_audit_run("acme", repo="acme/widgets", force=True, confirm=True) == {
        "called": "context_audit_run"
    }
    assert fake_client.calls == [
        ("context_audit_status", ("acme",), {"refresh": False}),
        ("org_update", ("acme",), {"display_name": None, "settings": settings}),
        ("org_update", ("acme",), {"display_name": "Acme", "settings": None}),
        ("context_audit_run", ("acme",), {"repo": None, "force": False, "limit": 2}),
        ("context_audit_run", ("acme",), {"repo": "acme/widgets", "force": True, "limit": None}),
    ]


async def test_context_audit_refusals_name_the_action(fake_client):
    update = await server.org_update("acme", settings={"context_audit_days": None, "context_audit_budget_usd": 5})
    one = await server.context_audit_run("acme", repo="acme/widgets", force=True)
    every = await server.context_audit_run("acme", limit=10)
    assert "org_update" in update["message"]
    assert "'acme': context_audit_budget_usd, context_audit_days" in update["message"]
    assert "context_audit_run" in one["message"] and "'acme/widgets'" in one["message"]
    assert "the due context audits under 'acme'" in every["message"]
    assert fake_client.calls == []


def test_org_update_names_every_setting_an_organization_accepts():
    """The docstring is the only list a client has: a setting the validator takes and the
    docstring leaves out is one nobody configures from an MCP client."""
    doc = server.org_update.__doc__ or ""
    missing = sorted(key for key in SETTINGS if key not in doc)
    assert missing == [], f"org_update does not name {missing}"


def test_the_instructions_say_context_audits_exist():
    text = server.mcp.instructions or ""
    for name in ("org_update", "context_audit_status", "context_audit_run", "CLAUDE.md"):
        assert name in text, f"the server instructions do not name {name}"


async def test_credential_read_tools_pass_through(fake_client):
    assert await server.credential_list() == {"called": "credential_list"}
    assert await server.credential_resolve("acme/widgets", role="forge") == {"called": "credential_resolve"}
    assert fake_client.calls == [
        ("credential_list", (), {}),
        ("credential_resolve", ("acme/widgets", "forge"), {}),
    ]


def test_credential_tool_docstrings_never_return_a_secret():
    for name in ("credential_list", "credential_resolve", "credential_bind"):
        doc = getattr(server, name).__doc__ or ""
        folded = doc.lower()
        assert "reveal" not in folded, name
        assert "never returns a secret" in folded, name


async def test_repo_register_forwards_forge_to_the_client(fake_client):
    assert await server.repo_register("g/s/p", forge="gitlab", confirm=True) == {"called": "repo_register"}
    name, args, kwargs = fake_client.calls[-1]
    assert name == "repo_register"
    assert args == ("g/s/p",)
    assert kwargs == {"notes": "", "forge": "gitlab"}


def test_no_tool_docstring_promises_a_two_segment_repo_path():
    """The docstrings ARE the contract an agent reads. embry0 drives a
    self-managed GitLab whose projects live at "group/subgroup/name"; a
    docstring saying "owner/name" is the single thing most likely to make an
    agent refuse — or silently truncate — the path job_create needs, and
    job_create is the only trigger there is."""
    offenders = sorted(
        name
        for name in EXPECTED_TOOLS
        if "owner/name" in (getattr(server, name).__doc__ or "")
        or "owner/repo" in (getattr(server, name).__doc__ or "")
    )
    assert offenders == []


def test_the_repo_shaped_tools_describe_the_subgroup_path():
    for name in ("issue_list", "issue_create", "job_list", "job_create", "env_list", "qa_config_get", "repo_get"):
        doc = getattr(server, name).__doc__ or ""
        assert "group/subgroup" in doc, f"{name} does not spell out a GitLab subgroup path"


async def test_case_tools_mirror_the_read_api(fake_client):
    assert await server.case_list(repo="acme/widgets", stage="merged", open_only=True, limit=5) == {
        "called": "case_list"
    }
    assert await server.case_get("hc-1") == {"called": "case_get"}
    assert fake_client.calls == [
        (
            "case_list",
            (),
            {
                "repo": "acme/widgets",
                "stage": "merged",
                "origin": None,
                "resolution_path": None,
                "healed_verdict": None,
                "fix_deployed": None,
                "open_only": True,
                "awaiting_decision": False,
                "limit": 5,
                "cursor": None,
            },
        ),
        ("case_get", ("hc-1",), {}),
    ]
    await server.case_list(awaiting_decision=True)
    assert fake_client.calls[-1][2]["awaiting_decision"] is True


def test_what_is_open_is_routed_by_the_descriptions():
    """An assistant asked "what is open?" picked case_list(open_only=true) —
    every finding not yet closed — because `open_only` was the only thing in
    the tool surface that said "open". The descriptions are the routing."""
    issues, cases = server.issue_list.__doc__ or "", server.case_list.__doc__ or ""
    for text in (server.mcp.instructions or "", issues, cases):
        assert 'issue_list(status="open")' in text or 'status="open"' in text
    assert "case_list(awaiting_decision=true)" in issues
    assert "awaiting_decision" in cases and "what needs a decision" in cases
    assert "case_list(awaiting_decision=true)" in (server.mcp.instructions or "")
    # open_only is described by what it includes, not left to its name.
    assert "merged, deployed and verified" in cases


async def test_merge_decide_relays_every_field(fake_client):
    result = await server.merge_decide("mrg-1", "approve", "Alex", "19:chat@thread.v2", confirm=True)
    assert result == {"called": "merge_decide"}
    assert fake_client.calls == [
        ("merge_decide", ("mrg-1",), {"decision": "approve", "decided_by": "Alex", "chat_id": "19:chat@thread.v2"})
    ]


async def test_merge_decide_without_confirm_does_nothing(fake_client):
    result = await server.merge_decide("mrg-1", "approve", "Alex", "19:chat@thread.v2")
    assert result["error"] == "confirmation_required"
    assert fake_client.calls == []


async def test_case_accept_relays_the_case_and_the_person(fake_client):
    result = await server.case_accept("hc-1", "Alex", confirm=True)
    assert result == {"called": "case_accept"}
    assert fake_client.calls == [("case_accept", ("hc-1",), {"accepted_by": "Alex"})]


def test_case_accept_says_a_linear_draft_is_accepted_here():
    """#460. The description is what the chat model acts on: while it said a
    Linear draft is accepted "by adding the label in Linear instead", the
    model sent people to Linear for something this tool now does."""
    doc = " ".join((server.case_accept.__doc__ or "").split())
    assert "by adding the label in Linear instead" not in doc
    assert "For a Linear draft embry0 puts the trigger label on the ticket itself" in doc
    # The one tracker still left to its label, and the promise the route keeps.
    assert "A GitLab draft is still accepted by adding the label in GitLab" in doc
    assert "there is no success without one" in doc


async def test_agent_create_forwards_every_field(fake_client):
    await server.agent_create(
        "lantern",
        "lights",
        "claude-sonnet-5",
        tools=["Read"],
        skills=["s"],
        system_prompt="p",
        execution_mode="sdk",
        auth_mode="oauth",
        mcp_servers={"x": {}},
        display_name="Lantern",
        avatar="bard",
        color="sky",
        tagline="Sings.",
        confirm=True,
    )
    assert fake_client.calls == [
        (
            "agent_create",
            ("lantern",),
            {
                "description": "lights",
                "model": "claude-sonnet-5",
                "tools": ["Read"],
                "skills": ["s"],
                "system_prompt": "p",
                "execution_mode": "sdk",
                "auth_mode": "oauth",
                "mcp_servers": {"x": {}},
                "display_name": "Lantern",
                "avatar": "bard",
                "color": "sky",
                "tagline": "Sings.",
            },
        )
    ]


async def test_agent_update_forwards_identity_fields(fake_client):
    await server.agent_update("developer", display_name="Dev", avatar="rogue", color="rose", tagline="t", confirm=True)
    name, args, kwargs = fake_client.calls[-1]
    assert (name, args) == ("agent_update", ("developer",))
    assert {k: kwargs[k] for k in ("display_name", "avatar", "color", "tagline")} == {
        "display_name": "Dev",
        "avatar": "rogue",
        "color": "rose",
        "tagline": "t",
    }


async def test_agent_delete_refusal_names_the_agent(fake_client):
    refusal = await server.agent_delete("lantern")
    assert refusal["error"] == "confirmation_required"
    assert "lantern" in refusal["message"] and "agent_delete" in refusal["message"]
    assert fake_client.calls == []


async def test_proposal_tools_relay_the_owner_and_refuse_without_confirm(fake_client):
    """Owner proposals (spec 2026-10-24): an answer names the owner who gave it, and only their yes sets confirm."""
    assert (await server.proposal_accept("issue:i-1", by="octo"))["error"] == "confirmation_required"
    assert (await server.proposal_decline("finding:ab", by="octo"))["error"] == "confirmation_required"
    assert (await server.proposal_snooze("case:hc-1", by="octo", until="2026-11-01T00:00:00Z"))[
        "error"
    ] == "confirmation_required"
    assert fake_client.calls == []
    await server.proposal_list(owner="octo", kind="finding")
    await server.proposal_accept("issue:i-1", by="octo", confirm=True)
    await server.proposal_decline("finding:ab", by="octo", reason="it says what I want", confirm=True)
    await server.proposal_snooze("case:hc-1", by="octo", until="2026-11-01T00:00:00Z", confirm=True)
    assert fake_client.calls == [
        ("proposal_list", (), {"owner": "octo", "kind": "finding", "since": None}),
        ("proposal_answer", ("issue:i-1", "accept"), {"by": "octo"}),
        ("proposal_answer", ("finding:ab", "decline"), {"by": "octo", "reason": "it says what I want"}),
        ("proposal_answer", ("case:hc-1", "snooze"), {"by": "octo", "reason": "", "until": "2026-11-01T00:00:00Z"}),
    ]

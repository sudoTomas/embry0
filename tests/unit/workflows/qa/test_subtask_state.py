def test_job_json_payload_carries_guardrails():
    """EMB-31: resolved guardrails ride job.json for the in-sandbox agent."""
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig
    from embry0.workflows.qa.subtask_state import _build_job_json_payload

    resolved = ResolvedAppConfig(
        app_name="hub",
        boot_command="npm start",
        frontend_url="http://localhost:3000",
        mode="process",
        sandbox_profile="qa-external",
        ready_checks=[],
        boot_timeout_seconds=60,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["home loads"],
        guardrails=["Never click Send"],
    )
    payload = _build_job_json_payload(
        sub_job_id="J__hub",
        attempt_n=1,
        qa_yaml={"mode": "process", "frontend_url": "http://localhost:3000"},
        resolved=resolved,
        sandbox_token="tok",
    )
    assert payload["guardrails"] == ["Never click Send"]

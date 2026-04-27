from athanor.execution.agent_runner import AgentOutput


def test_agent_output_dataclass():
    output = AgentOutput(
        agent_type="developer",
        is_error=False,
        output="Fixed the bug",
        cost_usd=0.42,
        duration_ms=15000,
        tools_called={"Read": 5, "Edit": 2},
    )
    assert output.agent_type == "developer"
    assert output.cost_usd == 0.42
    assert not output.is_error


def test_agent_output_defaults():
    output = AgentOutput(agent_type="test")
    assert output.is_error is False
    assert output.error_message == ""
    assert output.output == ""
    assert output.cost_usd == 0.0
    assert output.duration_ms == 0
    assert output.tools_called == {}

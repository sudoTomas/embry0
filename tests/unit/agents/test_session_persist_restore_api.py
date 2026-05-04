"""anthropic_api mode: capture messages from executor result; restore messages
into options for the next invocation."""


def test_capture_messages_from_api_mode_result():
    from athanor.agents.session import capture_session_after_run

    result = {
        "agent_type": "developer",
        "mode": "anthropic_api",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hi back"},
        ],
    }
    sess = capture_session_after_run(result, mode="anthropic_api", job_id="J", agent_type="developer")
    assert sess.mode == "anthropic_api"
    assert sess.messages == result["messages"]
    assert sess.session_id is None


def test_restore_messages_into_options_api():
    from athanor.agents.session import AgentSession, restore_session_into_options

    sess = AgentSession(
        job_id="J",
        agent_type="developer",
        mode="anthropic_api",
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi back"}],
    )
    options = {}
    out = restore_session_into_options(sess, options)
    assert out["messages"] == sess.messages


def test_round_trip_api():
    from athanor.agents.session import (
        capture_session_after_run,
        restore_session_into_options,
    )

    captured = capture_session_after_run(
        {"messages": [{"role": "user", "content": "x"}]},
        mode="anthropic_api",
        job_id="J",
        agent_type="developer",
    )
    options = restore_session_into_options(captured, {})
    assert options["messages"] == [{"role": "user", "content": "x"}]

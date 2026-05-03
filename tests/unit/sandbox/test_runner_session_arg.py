"""Runner --session-blob arg: when present, the executor receives the
prior conversation state."""

import json


def test_runner_passes_messages_to_executor_when_blob_given(tmp_path):
    from athanor.sandbox.runner import _build_run_kwargs

    blob = tmp_path / "msgs.json"
    blob.write_text(json.dumps([{"role": "user", "content": "hi"}]))

    config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": "api_key"}
    kwargs = _build_run_kwargs(config, session_blob_path=str(blob), session_id=None)
    assert kwargs["resume_messages"] == [{"role": "user", "content": "hi"}]


def test_runner_ignores_blob_when_not_given():
    from athanor.sandbox.runner import _build_run_kwargs

    config = {"agent_type": "developer", "execution_mode": "sdk", "auth_mode": "api_key"}
    kwargs = _build_run_kwargs(config, session_blob_path=None, session_id=None)
    assert kwargs.get("resume_messages") is None

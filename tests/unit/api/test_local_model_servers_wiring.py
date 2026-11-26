"""Production checks the custom provider's servers at startup and names the retired variables, and a resumed job on the
custom provider is enrolled with its credential's server again (ADR 0032).

Each piece constructs and runs happily without the other: a startup that never schedules the check leaves a stored
credential with a placeholder address to fail its first job silently, and a resume that never passes the credential
leaves its sandbox with no server at the llm-proxy. So this reads the AST of the two callers, as
test_background_models_wiring.py does.
"""

from __future__ import annotations

import ast
import pathlib

import embry0.api.app as app_module
import embry0.services._executor_run as executor_run


def _calls(module: object, name: str) -> list[ast.Call]:
    tree = ast.parse(pathlib.Path(module.__file__).read_text())  # type: ignore[attr-defined]
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def test_startup_schedules_the_check_of_every_stored_custom_credential() -> None:
    tasks = [
        ast.unparse(call.args[0])
        for call in _calls(app_module, "create_task")
        if call.args and "check_custom_servers" in ast.unparse(call.args[0])
    ]
    assert tasks == ["check_custom_servers(app.state.credentials_repo, app.state.secrets_provider)"], (
        f"startup must probe the stored custom credentials once, in the background: {tasks}"
    )


def test_startup_names_the_retired_variables() -> None:
    warned = [ast.unparse(call) for call in _calls(app_module, "warn_retired_upstream")]
    assert warned == ["warn_retired_upstream(config)"], f"LOCAL_LLM_UPSTREAM must be named while set: {warned}"


def test_a_resume_enrolls_a_custom_job_s_sandbox_with_its_credential_again() -> None:
    [reenroll] = _calls(executor_run, "reenroll")
    passed = [ast.unparse(kw.value) for kw in reenroll.keywords if kw.arg is None]
    assert passed == ["{'custom_credential_id': llm_ids['custom']} if 'custom' in llm_ids else {}"], (
        f"the resume must hand reenroll the custom credential it resolved: {passed}"
    )

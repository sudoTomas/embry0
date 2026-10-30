"""The ``background_models`` section (#164): one map of the models background features run on.

- It reads as an operator writes it: a default, each feature's own model, null for the default, false for none.
- Each value's shape is checked wherever it is read, naming its key and never repeating the value.
- Whether the provider catalogue offers a model, and whether it can serve the features that would run on it,
  is checked on a write (the store's write context), never when a stored value is read back or offline.

The section is taken from the application's catalog by name, so each test fails with its own message on
a tree that has no such section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG

FEATURES = ("watcher", "context_audit", "scout", "proposal_brief", "job_brief", "slop_judge")


def _section() -> Section[Any]:
    section = CATALOG.get("background_models")
    assert section is not None, "background_models is a section of the settings document (#164)"
    return section


def _write_context() -> dict[str, Any]:
    _section()
    from embry0.settings.write_context import deployment_context

    return deployment_context()


def _stored(document: dict[str, Any], *, context: dict[str, Any] | None = None) -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, msg)``."""
    _section()
    try:
        return check(CATALOG, document, stored={}, context=context).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def _model_of(values: dict[str, Any], feature: str) -> str:
    _section()
    from embry0.settings.sections.background_models import model_of

    model = read_sections(CATALOG, {"background_models": values}).models["background_models"]
    return model_of(model, feature)  # type: ignore[arg-type]


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai,custom")


def test_the_section_reads_as_an_operator_writes_it() -> None:
    text = (
        "background_models:\n"
        "  default: custom/qwen3-32b  # every feature unless it names its own\n"
        "  scout: false\n"
        "  proposal_brief: gpt-6-luna\n"
        "  job_brief: null\n"
    )
    assert _stored(load_yaml(text)) == {
        "background_models": {
            "default": "custom/qwen3-32b",
            "scout": False,
            "proposal_brief": "gpt-6-luna",
            "job_brief": None,
        }
    }


def test_null_runs_a_feature_on_the_default_false_on_none_and_no_feature_falls_back_on_another() -> None:
    values = {"default": "custom/local", "scout": False, "proposal_brief": "gpt-6-luna"}
    assert {feature: _model_of(values, feature) for feature in FEATURES} == {
        "watcher": "custom/local",
        "context_audit": "custom/local",
        "scout": "",
        "proposal_brief": "gpt-6-luna",
        "job_brief": "custom/local",
        "slop_judge": "custom/local",
    }, "the job briefs and the judge read the default, never the proposal briefs' model (the retired chain)"
    assert {feature: _model_of({}, feature) for feature in FEATURES} == dict.fromkeys(FEATURES, ""), (
        "nothing written: every feature runs on no model, as nothing set ran on none before"
    )


def test_the_scout_runs_only_on_a_model_named_for_it() -> None:
    """Setting the default must not start the scout: it is opt-in, false until a model is named for it (#287)."""
    assert _model_of({"default": "custom/local"}, "scout") == "", "a default alone leaves the scout off"
    assert _model_of({"default": "custom/local", "scout": "custom/scout"}, "scout") == "custom/scout"
    assert _model_of({"default": "custom/local", "scout": None}, "scout") == "custom/local", (
        "null runs it on the default, as every other feature's null does"
    )


def test_a_bad_shape_is_refused_by_its_key_and_never_repeated() -> None:
    found = _stored(
        {
            "background_models": {
                "default": False,
                "watcher": True,
                "scout": 7,
                "job_brief": "two words",
                "slop_judge": "",
            }
        }
    )
    assert isinstance(found, list), f"each bad value is refused when it is written: {found}"
    by_key = {loc[1]: msg for loc, msg in found}
    assert sorted(by_key) == ["default", "job_brief", "scout", "slop_judge", "watcher"], found
    assert by_key["default"] == "write a model id, or null for none", "the default has no false: null is none"
    assert by_key["watcher"] == "write a model id, null to run on the default, or false for no model"
    assert "no spaces" in by_key["job_brief"] and "no spaces" in by_key["slop_judge"]
    assert all(len(loc) == 2 for loc, _ in found), f"one problem per key, not one per member of a union: {found}"
    assert "two words" not in str(found), "a refusal never repeats the value"


def test_a_model_id_ending_in_a_newline_is_refused() -> None:
    found = _stored({"background_models": {"scout": "custom/local\n"}})
    assert isinstance(found, list) and [loc for loc, _ in found] == [("background_models", "scout")], found


def test_the_catalogue_is_checked_on_a_write_naming_each_key(providers: None) -> None:
    document = {
        "background_models": {
            "default": "custom/qwen",
            "context_audit": "custom/qwen",
            "scout": "claude-sonnet-5",
            "proposal_brief": "gpt-6.1-sol",
            "job_brief": "grok-4.7",
            "slop_judge": "no-such-model",
        }
    }
    found = _stored(document, context=_write_context())
    assert isinstance(found, list), f"a write is checked against the provider catalogue: {found}"
    by_key = {loc[1]: msg for loc, msg in found}
    assert sorted(by_key) == ["job_brief", "slop_judge"], (
        "the scout pins an Anthropic model, the local models are offered, and the proposal briefs are asked "
        f"over chat completions, which a chat-only model serves: only the rest are refused: {found}"
    )
    assert "chat completions" in by_key["job_brief"]
    assert "not a model this deployment offers" in by_key["slop_judge"]
    assert "no-such-model" not in str(found), "a refusal never repeats the value"


@pytest.mark.parametrize("key", ["default", "watcher", "context_audit"])
@pytest.mark.parametrize("model", ["gpt-6-luna", "claude-sonnet-5", "grok-4.7", "openrouter/openai/gpt-6.1-sol"])
def test_a_preference_takes_only_a_model_of_the_operator_s_own_server(key: str, model: str) -> None:
    """A preference for a hosted model is a pin: it never runs out of slots, so it is refused, by its key (#287).
    Offline too: the rule needs no catalogue."""
    found = _stored({"background_models": {key: model}})
    assert isinstance(found, list) and [loc for loc, _ in found] == [("background_models", key)], (
        f"{key} takes only a custom/<model>: {found}"
    )
    assert "custom/<model>" in found[0][1] and model not in found[0][1]
    assert isinstance(_stored({"background_models": {key: "custom/"}}), list), "the prefix alone is no model"


@pytest.mark.parametrize("key", ["scout", "proposal_brief", "job_brief", "slop_judge"])
def test_a_hosted_model_may_be_named_for_the_briefs_the_judge_and_the_scout(providers: None, key: str) -> None:
    assert _stored({"background_models": {key: "gpt-6-luna"}}, context=_write_context()) == {
        "background_models": {key: "gpt-6-luna"}
    }, "hosted models are allowed where their calls and jobs are metered and capped"


def test_the_default_must_serve_every_feature(providers: None) -> None:
    context = _write_context()
    assert _stored({"background_models": {"default": "custom/qwen"}}, context=context) == {
        "background_models": {"default": "custom/qwen"}
    }


def test_a_local_model_needs_the_custom_provider_on_a_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "")
    found = _stored({"background_models": {"default": "custom/qwen"}}, context=_write_context())
    assert isinstance(found, list) and "custom provider enabled" in found[0][1], found


def test_a_stored_model_the_catalogue_no_longer_offers_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading never consults the catalogue: OpenRouter's live list arrives after startup, and a provider can be
    turned off. Either would otherwise turn a stored choice into null, the feature into none, silently."""
    _section()
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "")
    sections = read_sections(
        CATALOG, {"background_models": {"default": "custom/gone", "job_brief": "openrouter/acme/new-model"}}
    )
    assert sections.refused == (), f"nothing is refused when it is read: {sections.refused}"
    assert _model_of({"job_brief": "openrouter/acme/new-model"}, "job_brief") == "openrouter/acme/new-model"


def test_offline_the_shape_is_checked_and_the_catalogue_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "")
    assert _stored({"background_models": {"default": "custom/qwen"}}) == {
        "background_models": {"default": "custom/qwen"}
    }, "`embry0 settings check` runs where the deployment's providers are not known; the write checks them"


def test_an_organization_may_set_every_key_and_a_repository_none() -> None:
    section = _section()
    assert section.org_keys == {"default", *FEATURES}
    assert section.repo_keys == frozenset()


def test_the_variables_are_imported_into_their_feature_s_key_and_an_empty_one_is_the_default() -> None:
    """An empty SCOUT_MODEL is the scout's default, false, as the scout ran with none."""
    from embry0.settings.env import env_name

    imports = _section().imports()
    assert {key: env_name(imported.attr) for key, imported in imports.items()} == {
        "context_audit": "CONTEXT_AUDIT_LOCAL_MODEL",
        "scout": "SCOUT_MODEL",
        "proposal_brief": "PROPOSAL_BRIEF_MODEL",
        "job_brief": "JOB_BRIEF_MODEL",
    }
    convert = imports["scout"].convert
    assert convert is not None and convert(" custom/qwen ") == "custom/qwen" and convert("") is False
    other = imports["proposal_brief"].convert
    assert other is not None and other("") is None


def test_a_stored_model_its_provider_no_longer_offers_never_blocks_a_change_to_another_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _section()
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openai")
    stored = {"background_models": {"default": "custom/qwen"}}
    document = {"background_models": {"default": "custom/qwen", "scout": False}}
    assert check(CATALOG, document, stored=stored, context=_write_context()).data == document, (
        "the write leaves the default as it was, so it is not checked against the catalogue again"
    )
    with pytest.raises(DocumentInvalid) as refused:
        check(CATALOG, {"background_models": {"default": "custom/other"}}, stored=stored, context=_write_context())
    assert [problem.loc for problem in refused.value.problems] == [("background_models", "default")], (
        "a value the write sets is checked"
    )

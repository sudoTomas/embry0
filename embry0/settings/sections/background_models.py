"""The ``background_models`` section: the models embry0's background features run on.

One map, with a default and one key per feature::

    background_models:
      default: custom/qwen3-32b   # what the features below run on unless they name their own
      scout: custom/qwen3-32b     # the scout runs only on a model named for it
      proposal_brief: gpt-6-luna  # a hosted model; its spend counts against the budget caps

A feature's key is a model id, ``null`` to run on ``default``, or ``false``
for no model at all. Left out, it runs on ``default``, except ``scout``,
which is ``false`` until a model is named for it: setting the default never
starts the scout. ``default`` is a model id or ``null``, no model. No feature
falls back on another: changing one feature's model never changes another's
unless that one runs on the default.

What each feature does with its model, and with none:

- ``watcher`` and ``context_audit`` start jobs that *prefer* the model: their
  agents run on it while one of the operator's server's slots is free, and
  on their own models otherwise. With none they run on their own. A
  preference for a hosted model would be a pin (it never runs out of slots),
  so these two, and ``default``, which they run on when they name nothing,
  take only a model of the operator's own server, ``custom/<model>``.
- ``scout`` *pins* its jobs to the model: a run waits for it. With none the
  scout starts nothing.
- ``proposal_brief``, ``job_brief`` and ``slop_judge`` ask the model directly,
  over chat completions, outside any job. With none nothing is written or
  judged. A call to a hosted model is counted against the daily and monthly
  caps and refused once they are reached (``embry0.services.background_models``).

Each value's shape is checked whenever it is read: a model id, ``custom/``
where the key takes only a local model. Whether the deployment's provider
catalogue offers it, and whether it can serve its feature, is checked when a
write sets it: through the validation context the settings store passes
(:data:`CATALOGUE`), never when a stored value is read back or left as it
was, at the environment import or offline, so a catalogue that moves
(OpenRouter's live list arrives after startup; a provider is turned off)
never turns a stored choice into ``null`` nor blocks a change to another key.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Final, Literal

from pydantic import Field, ValidationInfo, field_validator

from embry0.settings.registry import STORED, EnvImport, Section, SectionModel

#: The validation context key under which a write passes the catalogue check:
#: ``(model, needs) -> problem or None`` (``embry0.settings.write_context``).
CATALOGUE: Final = "model_catalogue"

#: What a feature needs of its model:
#: ``tools``: its agents call tools, so a chat-only model will not do;
#: ``chat``: it is asked directly, over chat completions.
Need = Literal["tools", "chat"]

#: The features, in the order the section lists them, and what each needs of its model.
FEATURES: Final[Mapping[str, frozenset[Need]]] = {
    "watcher": frozenset({"tools"}),
    "context_audit": frozenset({"tools"}),
    "scout": frozenset({"tools"}),
    "proposal_brief": frozenset({"chat"}),
    "job_brief": frozenset({"chat"}),
    "slop_judge": frozenset({"chat"}),
}
#: The default serves every feature that names no model of its own, so it needs what each of them does.
DEFAULT_NEEDS: Final[frozenset[Need]] = frozenset(need for needs in FEATURES.values() for need in needs)
#: The keys that take only a model of the operator's own server: a preference for a hosted model is a pin.
LOCAL_ONLY: Final = frozenset({"default", "watcher", "context_audit"})
#: The routing prefix of the operator's own server (``embry0.agents.providers.CUSTOM``).
LOCAL_PREFIX: Final = "custom/"

#: The longest model id accepted.
MAX_LENGTH: Final = 200
_MODEL_ID: Final = re.compile(rf"\S{{1,{MAX_LENGTH}}}")
_SHAPE: Final = f"a model id is one word, with no spaces, of at most {MAX_LENGTH} characters"
_LOCAL: Final = (
    "a model of your own server, custom/<model>: this key's model is preferred while a local slot is free, "
    "and a preference for a hosted model would pin every run to it. Name a hosted model for the briefs, "
    "the judge or the scout instead"
)

ModelId = Annotated[str, Field(max_length=MAX_LENGTH, pattern=r"^\S+$")]
LocalModelId = Annotated[str, Field(max_length=MAX_LENGTH, pattern=r"^custom/\S+$")]

#: ``(model, needs) -> problem``: the catalogue check a write passes in the validation context.
Checker = Callable[[str, frozenset[Need]], str | None]


def env_model(value: Any) -> str | None:
    """A ``*_MODEL`` variable as the section stores it: its model id, or None (the default) when empty."""
    text = str(value or "").strip()
    return text or None


def env_scout_model(value: Any) -> str | Literal[False]:
    """``SCOUT_MODEL`` as the section stores it: its model id, or ``false`` when empty, as the scout ran."""
    return env_model(value) or False


class BackgroundModels(SectionModel):
    default: LocalModelId | None = Field(
        default=None,
        description=(
            "The model of your own server, custom/<model>, the features below run on unless they name their "
            "own; null for none. It must call tools and answer over chat completions, since any of them may "
            "run on it. The scout never does unless it is named for it."
        ),
    )
    watcher: LocalModelId | Literal[False] | None = Field(
        default=None,
        description=(
            "The model of your own server the watcher's log analyses prefer: they run on it while a local "
            "slot is free, else on their agents' own models. null: the default; false: no preference."
        ),
    )
    context_audit: LocalModelId | Literal[False] | None = Field(
        default=None,
        description=(
            "The model of your own server scheduled context audits prefer, as the watcher's analyses do. A "
            "model chosen with Run with wins. null: the default; false: no preference."
        ),
    )
    scout: ModelId | Literal[False] | None = Field(
        default=False,
        description=(
            "The model every scouting job is pinned to, a run waiting for it; a hosted one's jobs are metered "
            "and capped like any job. false, the default: no model, and the scout starts nothing; null: the "
            "default model."
        ),
    )
    proposal_brief: ModelId | Literal[False] | None = Field(
        default=None,
        description=(
            "The model that writes each owner proposal's plain-words brief, asked over chat completions; a "
            "hosted one's calls count against the budget caps. null: the default; false: none is written, "
            "and the feed shows each proposal's summary."
        ),
    )
    job_brief: ModelId | Literal[False] | None = Field(
        default=None,
        description=(
            "The model that writes each job's brief, asked over chat completions; a hosted one's calls count "
            "against the budget caps. null: the default; false: none is written."
        ),
    )
    slop_judge: ModelId | Literal[False] | None = Field(
        default=None,
        description=(
            "The model the Slop-O-Meter asks whether an outlier commit is slop, over chat completions; a "
            "hosted one's calls count against the budget caps. null: the default; false: outliers stay "
            "unjudged and the score provisional."
        ),
    )

    @field_validator("default", *FEATURES, mode="before")
    @classmethod
    def _shape(cls, value: Any, info: ValidationInfo) -> Any:
        """One plain message per key, instead of one per member of the type's union."""
        name = str(info.field_name)
        if value is None or (value is False and name != "default"):
            return value
        if not isinstance(value, str):
            if name == "default":
                raise ValueError("write a model id, or null for none")
            raise ValueError("write a model id, null to run on the default, or false for no model")
        if _MODEL_ID.fullmatch(value) is None:
            raise ValueError(_SHAPE)
        if name in LOCAL_ONLY and (not value.startswith(LOCAL_PREFIX) or len(value) == len(LOCAL_PREFIX)):
            raise ValueError(_LOCAL)
        return value

    @field_validator("default", *FEATURES)
    @classmethod
    def _offered(cls, value: Any, info: ValidationInfo) -> Any:
        """On a write, a model the deployment's catalogue offers and that can serve the feature."""
        context = info.context if isinstance(info.context, Mapping) else {}
        checker: Checker | None = context.get(CATALOGUE)
        if checker is None or not isinstance(value, str):
            return value
        name = str(info.field_name)
        before = context.get(STORED)
        if isinstance(before, Mapping) and before.get(name) == value:
            # Not written by this change: a model stored before its provider was turned off never
            # blocks a change to another key. The feature says so when it cannot call it.
            return value
        problem = checker(value, DEFAULT_NEEDS if name == "default" else FEATURES[name])
        if problem:
            if name == "default":
                problem += "; the default serves every feature that names no model of its own"
            raise ValueError(problem)
        return value


def model_of(models: BackgroundModels, feature: str) -> str:
    """The model ``feature`` runs on: its own, else the default; '' for none (``false``, or no default)."""
    own = getattr(models, feature)
    if own is False:
        return ""
    if isinstance(own, str):
        return own
    return models.default or ""


BACKGROUND_MODELS = Section(
    name="background_models",
    model=BackgroundModels,
    summary="The models embry0's background features run on: a default, and each feature's own.",
    # An organization may choose the models its own repositories' background
    # work runs on: it may have bound keys of its own (a call resolves its key
    # with the repository's bindings), and every call on a hosted model is
    # still counted against the deployment's daily and monthly caps. A
    # repository may not: a key whose allowlist covers one repository could
    # otherwise move the deployment's shared spend.
    org_keys=frozenset({"default", *FEATURES}),
    repo_keys=frozenset(),
    env={
        "context_audit": EnvImport("context_audit_local_model", env_model),
        "scout": EnvImport("scout_model", env_scout_model),
        "proposal_brief": EnvImport("proposal_brief_model", env_model),
        "job_brief": EnvImport("job_brief_model", env_model),
    },
)

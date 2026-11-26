"""The ``local_models`` section: the limits of the operator's own model servers (#174).

A custom credential is a model server, its address and key (ADR 0032,
docs/providers.md, local models). One set of limits applies to every one of
them, for the whole deployment::

    local_models:
      max_runs: 1                   # calls each server takes at once
      slot_wait_seconds: 900        # how long a run that has to use a server waits for a slot
      request_timeout_seconds: 900  # how long the local-llm-proxy waits for a server's answer
      max_request_bytes: 8388608    # the largest request body the proxy forwards

Every value is read where it is used, so a change needs no restart:
``max_runs`` and ``slot_wait_seconds`` when a run or a background call takes a
slot of its server (``services.local_slots``); the request cap and the timeout
when a sandbox is enrolled with the local-llm-proxy, so a change reaches the
next sandbox, never past the ceilings the proxy starts with.

Before this section ``max_runs`` was ``provider_config.local_max_runs`` (a
stored value was carried here at the upgrade), and the wait and the cap were
the variables ``LOCAL_LLM_SLOT_WAIT_SECONDS`` and
``LOCAL_LLM_MAX_REQUEST_BYTES``, no longer read (:mod:`embry0.settings.env`).
A model's context window is the provider catalogue's ``context_window`` of
each custom model, in place of ``LOCAL_LLM_CONTEXT_TOKENS``.
"""

from __future__ import annotations

from typing import Final

from pydantic import Field

from embry0.settings.registry import Section, SectionModel

#: The most a sandbox may be allowed, whatever this section says: the local-llm-proxy reads no larger body and waits
#: no longer (``execution.proxy.local_llm_proxy``'s ceilings, which a test holds equal to these).
MAX_REQUEST_BYTES_CEILING: Final = 64 * 1024 * 1024
TIMEOUT_SECONDS_CEILING: Final = 3600


class LocalModelsSettings(SectionModel):
    max_runs: int = Field(
        default=1,
        ge=1,
        le=64,
        description=(
            "How many calls each of the operator's model servers takes at once: agent runs and background calls "
            "alike. A server is the custom credential a call is made with. A run that only prefers a local model "
            "runs on its own model when no slot is free; one that has to use it waits slot_wait_seconds."
        ),
    )
    slot_wait_seconds: int = Field(
        default=900,
        ge=0,
        le=86400,
        description=(
            "How long a run that has to use a local model (the fallback, or the model a person named) waits for a "
            "free slot of its server before the job is parked for five minutes. 0: it does not wait."
        ),
    )
    request_timeout_seconds: int = Field(
        default=900,
        ge=10,
        le=TIMEOUT_SECONDS_CEILING,
        description=(
            "How long the local-llm-proxy waits for a server to answer a sandbox's call; a long completion from a "
            "model on one card can take minutes. Read when a sandbox is enrolled: a change reaches the next one."
        ),
    )
    max_request_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=MAX_REQUEST_BYTES_CEILING,
        description=(
            "The largest request body the local-llm-proxy forwards to a server, in bytes; over it the call is "
            "refused with 413 and the run ends as ERR_CONTEXT_WINDOW_EXCEEDED. Every turn resends the whole "
            "conversation. Read when a sandbox is enrolled: a change reaches the next one."
        ),
    )


LOCAL_MODELS = Section(
    name="local_models",
    model=LocalModelsSettings,
    summary="The limits of the operator's own model servers: runs at once, the wait for a slot, the proxy's "
    "timeout and request cap.",
    # A server is shared by every organization whose credential reaches it: its limits are the deployment's.
    org_keys=frozenset(),
    repo_keys=frozenset(),
)

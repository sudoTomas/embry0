"""qa_failure_action validation + the repair prompt (#304).

After a QA failure triage must embed exactly one ``qa_failure_action`` in its
decision JSON. This module owns the schema check so the triage node can run it
twice — once on the agent's answer and once on the single repair answer —
without duplicating the rules. It knows nothing about routing or state.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ValidationError

from embry0.agents.triage_actions import (
    AskUser,
    QAFailureAction,
    RerunQA,
    RetryDeveloper,
    clamp_advisory_strings,
)

logger = structlog.get_logger(__name__)

InvalidReason = Literal["missing", "unknown_kind", "invalid_payload"]

_MODEL_BY_KIND: dict[str, type[BaseModel]] = {
    "retry_developer": RetryDeveloper,
    "rerun_qa": RerunQA,
    "ask_user": AskUser,
}

_CONTRACT = (
    '  {"kind": "retry_developer", "prompt": "<10-4000 chars>", "focus_files": ["<path>", ...]}\n'
    '  {"kind": "rerun_qa", "reason": "<1-500 chars>"}\n'
    '  {"kind": "ask_user", "question": "<10-2000 chars>"}'
)


class QAFailureActionInvalid(Exception):
    """The decision's ``qa_failure_action`` is absent or fails its schema.

    ``reason`` buckets the failure for logs; ``detail`` is the human/agent
    readable explanation that the repair prompt feeds back.
    """

    def __init__(self, reason: InvalidReason, detail: str, *, kind: str | None = None, rejected: Any = None) -> None:
        self.reason: InvalidReason = reason
        self.detail = detail
        self.kind = kind
        self.rejected = rejected  # the raw qa_failure_action value, for the repair prompt
        super().__init__(f"{reason}: {detail}")


def parse_qa_failure_action(triage_decision: dict[str, Any] | None) -> QAFailureAction:
    """Return the validated action model, or raise ``QAFailureActionInvalid``."""
    raw = triage_decision.get("qa_failure_action") if isinstance(triage_decision, dict) else None
    if not isinstance(raw, dict):
        raise QAFailureActionInvalid(
            "missing",
            f"qa_failure_action must be a JSON object, got {type(raw).__name__}",
            rejected=raw,
        )

    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _MODEL_BY_KIND:
        raise QAFailureActionInvalid(
            "unknown_kind",
            f"qa_failure_action.kind must be one of {sorted(_MODEL_BY_KIND)}, got {kind!r}",
            rejected=raw,
        )

    model_cls = _MODEL_BY_KIND[kind]
    # Strip kind before model validation; the action models don't carry it.
    payload = _drop_empty_sibling_fields(kind, {k: v for k, v in raw.items() if k != "kind"})
    # RAV-1248: reason/prompt/question are advisory context, not control flow —
    # an over-long explanation is clamped, never a validation failure.
    payload = clamp_advisory_strings(model_cls, payload)
    try:
        action = model_cls.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or kind}: {err['msg']}" for err in exc.errors()
        )
        raise QAFailureActionInvalid("invalid_payload", f"kind={kind}: {problems}", kind=kind, rejected=raw) from exc
    assert isinstance(action, RetryDeveloper | RerunQA | AskUser)  # narrows BaseModel for mypy
    return action


def _drop_empty_sibling_fields(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys that belong to ANOTHER kind's schema and carry no value.

    job-d05a769e7865 died on ``ask_user`` + ``focus_files: []`` — the agent
    filling in a sibling kind's optional field with its empty default. That
    says nothing and costs nothing to ignore. A NON-empty sibling field
    (``rerun_qa`` that also names files to fix) signals confused intent and an
    unknown key signals schema drift: both stay validation errors so the
    repair turn — and the logs — see them.
    """
    own = set(_MODEL_BY_KIND[kind].model_fields)
    sibling = {name for other in _MODEL_BY_KIND.values() for name in other.model_fields} - own
    dropped = sorted(k for k, v in payload.items() if k in sibling and v in (None, "", [], {}))
    if not dropped:
        return payload
    logger.warning("triage_qa_failure_action_empty_sibling_fields_dropped", kind=kind, fields=dropped)
    return {k: v for k, v in payload.items() if k not in dropped}


_REJECTED_PREVIEW_CHARS = 2000


def build_repair_prompt(invalid: QAFailureActionInvalid) -> str:
    """The one-shot repair turn: the rejected answer, what was wrong with it,
    and the exact contract. Self-contained — on the non-resumable path the
    model has no memory of what it sent."""
    if invalid.rejected is None:
        rejected_block = "Your triage decision did not include a qa_failure_action."
    else:
        rejected = json.dumps(invalid.rejected, default=str)[:_REJECTED_PREVIEW_CHARS]
        rejected_block = f"You answered the QA failure with this qa_failure_action:\n  {rejected}"
    return (
        f"{rejected_block}\n\n"
        "It failed validation and was NOT applied:\n"
        f"  {invalid.detail}\n\n"
        "It must be exactly one of these shapes, with no other fields:\n"
        f"{_CONTRACT}\n\n"
        "Do not investigate further. Reply with ONLY the corrected qa_failure_action "
        "JSON object; the rest of your triage decision stands as it is."
    )


def extract_repaired_action(raw_answer: str) -> Any:
    """The action object in a repair answer, or None when there is none.

    Lenient about the envelope so the paid retry is not wasted on format: the
    bare action object (what the repair prompt asks for), ``{"qa_failure_action":
    {...}}``, or a full triage decision all work. The action itself still goes
    through ``parse_qa_failure_action``.
    """
    from embry0.orchestration.nodes.triage import _extract_json_payload

    try:
        data = json.loads(_extract_json_payload(raw_answer))
    except (ValueError, RecursionError, IndexError):  # see embry0.json_text
        return None
    if not isinstance(data, dict):
        return None
    if "qa_failure_action" in data:
        return data["qa_failure_action"]
    return data if "kind" in data else None

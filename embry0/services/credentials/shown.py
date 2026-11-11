"""A credential resolution as a reader that does not read every binding scope sees it.

A resolution (:mod:`embry0.services.credentials.resolver`) walks the bindings
of the repository, then its owner's, the forge host's and the global pool,
then the environment's tokens, and keeps each step in its ``trace``. A key
with a repository allowlist reads the bindings of a repository it covers, and
of an owner it covers as a whole, and none of the host's or the global pool's,
which are every repository's (``embry0.api.resolution_view.scopes_read`` says
which scopes a caller reads). Everything here takes those scopes, ``read``:
None is a reader of every scope, who sees a resolution unchanged.

To any other reader a step at a scope it does not read names no credential:
the trace keeps the steps it reads and one line, :data:`DEPLOYMENT_STEPS`, for
the rest; a credential resolved there is :data:`DEPLOYMENT_CREDENTIAL`, from
:data:`DEPLOYMENT_SOURCE`; and an unavailable credential pinned there answers
as nothing resolved does (:func:`as_missing`), so the answer does not tell
whether that scope pins one. The reader still learns whether the repository
resolves, and that what it resolves is the deployment's to choose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError

#: What a resolution names, to such a reader, in place of a credential of a scope it does not read.
DEPLOYMENT_CREDENTIAL: Final = "(the deployment's)"
#: The ``source`` such a credential is shown from, in place of ``registry`` or ``env``.
DEPLOYMENT_SOURCE: Final = "deployment"
#: The one trace step that stands, to such a reader, for every step at a scope it does not read.
DEPLOYMENT_STEPS: Final = "(the deployment's credentials)"

#: The binding scopes a reader reads; None for every scope.
Read = frozenset[str] | None


def step_scope(step: str) -> str:
    """The binding scope a trace step names: ``repo``, ``owner``, ``host``, ``global`` or ``env``, the text before
    its first ``:``, as the resolver writes each step (``<scope>:<value> -> <label>``, ``env:<key>``). Anything else
    it may write, such as a registry error, names no scope a restricted reader reads."""
    return step.partition(":")[0]


def trace_shown(trace: Sequence[str], read: Read) -> list[str]:
    """``trace`` as a reader of ``read`` sees it: every step for None; else the steps at a scope read, and
    :data:`DEPLOYMENT_STEPS` once for all the others."""
    if read is None:
        return list(trace)
    shown = [step for step in trace if step_scope(step) in read]
    return shown if len(shown) == len(trace) else [*shown, DEPLOYMENT_STEPS]


def resolved_where_read(trace: Sequence[str], read: Read) -> bool:
    """Whether a credential resolved with ``trace`` is one a reader of ``read`` may see named: always for None; else
    when the step that resolved it, the trace's last, is at a scope read."""
    return read is None or (bool(trace) and step_scope(trace[-1]) in read)


def label_shown(label: str, trace: Sequence[str], read: Read) -> str:
    """The label of a credential resolved with ``trace``, or :data:`DEPLOYMENT_CREDENTIAL` when a reader of ``read``
    does not read where it was resolved."""
    return label if resolved_where_read(trace, read) else DEPLOYMENT_CREDENTIAL


def resolution_shown(view: Mapping[str, Any], read: Read) -> dict[str, Any]:
    """A resolved credential, ``view`` (``id``, ``label``, ``source``, ``trace``, and ``host`` where it has one), as a
    reader of ``read`` sees it: unchanged for None. Otherwise its trace is :func:`trace_shown`'s, and when it was
    resolved at a scope not read it is named by no id, label or host: :data:`DEPLOYMENT_CREDENTIAL`, from
    :data:`DEPLOYMENT_SOURCE`."""
    if read is None:
        return dict(view)
    trace = [str(step) for step in view.get("trace") or ()]
    out = {**view, "trace": trace_shown(trace, read)}
    if not resolved_where_read(trace, read):
        out.update(id=None, label=DEPLOYMENT_CREDENTIAL, source=DEPLOYMENT_SOURCE)
        if "host" in out:
            out["host"] = ""
    return out


def as_missing(exc: Exception, read: Read) -> bool:
    """Whether a failed resolution answers a reader of ``read`` as one that found nothing: a missing credential, and,
    to a reader that does not read every scope, an unavailable one pinned at a scope it does not read, so the answer
    does not tell whether that scope pins one."""
    if isinstance(exc, CredentialMissingError):
        return True
    return (
        read is not None
        and isinstance(exc, CredentialUnavailableError)
        and exc.missing is not None
        and exc.scope_kind not in read
    )


def error_shown(exc: Exception, read: Read) -> str:
    """The message of a failed resolution as a reader of ``read`` sees it: what it says, then the trace
    (:func:`trace_shown`) when it has one. An unavailable pinned credential is named, with why, when the reader reads
    its scope; otherwise it answers as :func:`as_missing` says, with the missing credential's message."""
    if isinstance(exc, CredentialUnavailableError):
        if not as_missing(exc, read):
            return exc.detail if read is None or exc.scope_kind in read else str(exc)
        head, trace = str(exc.missing), exc.trace
    elif isinstance(exc, CredentialMissingError):
        head, trace = str(exc), exc.trace
    else:
        return str(exc)
    return f"{head} (trace: {'; '.join(trace_shown(trace, read))})" if trace else head

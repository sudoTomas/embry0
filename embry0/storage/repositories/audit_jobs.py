"""Which jobs are context audits: one predicate for progression, the findings feed and the scheduler (#167).

A job is an audit when the context auditor led it, the agent whose report
an audit's claims are read from: ``jobs.leader_agent``, which a job takes
from its template's row when it is created and plan_route records again
when it plans it. A finding's fix job (:data:`FIX_ACTOR`, #240) is led by
the context auditor too but is no audit: it fixes one claim. So a copy of
the context-audit template earns audit credit and offers its findings, and
a job of a template whose leader an operator changed to another agent does
neither.

The context audit's scheduler counts every audit, and also every job of its
own template and every job it dispatched, whatever led them
(``ContextAuditLoop._audits``): such a job works on the files an audit opens
a pull request on, and after an operator gives the template another leader
the scheduler must not start an audit again on every tick. It dispatches
its own template (``context_audit.TEMPLATE``).
"""

from __future__ import annotations

from embry0.orchestration.dispatch_actors import PROPOSALS

#: The agent a context audit is led by.
AUDITOR = "context-auditor"
#: The actor of a context-audit finding's fix job (``context_audit.FIX_ACTOR``): the job an owner starts by accepting
#: one finding. It is no audit, so it earns no audit's credit and offers no findings.
FIX_ACTOR = PROPOSALS


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def led_by_the_auditor(job: str) -> str:
    """The SQL condition that the job aliased ``job`` was led by the :data:`AUDITOR`: an audit or a fix job."""
    return f"{job}.leader_agent = {_literal(AUDITOR)}"


def an_audit(job: str) -> str:
    """The SQL condition that the job aliased ``job`` is a context audit: led by the :data:`AUDITOR`, and not a
    finding's fix job."""
    return f"({led_by_the_auditor(job)} AND {job}.dispatched_by IS DISTINCT FROM {_literal(FIX_ACTOR)})"


def is_audit(leader: str | None, dispatched_by: str | None) -> bool:
    """:func:`an_audit` for one job's ``leader_agent`` and ``dispatched_by``."""
    return leader == AUDITOR and dispatched_by != FIX_ACTOR

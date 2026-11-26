"""What a pipeline template's row, or an agent's, says about how embry0 treats it (#167).

Behaviour that depends on a template or an agent is a column of its row, not
a list of built-in names, so an operator's own templates and agents take
part the way the built-in ones do. A template's row says:

- ``fixed_route``: a job that names the template skips triage, whose choice
  is already made (``_triage.fixed_route_decision``);
- ``offered_to_triage``: triage's list of templates names it, and plan_route
  keeps triage's pick of it; a template that is not offered runs only when
  the job names it;
- ``job_kind``: the kind a job of the template is recorded as, an
  operator's override; when it is NULL the kind is the one the template is
  the default for, else the one its route makes it (a route of specialists
  only is analysis: :func:`recorded_kind`);
- ``delivers``: what a job of the template hands back, in the person's
  words, for the job's brief;
- ``leader_agent``: the character the world draws a job of the template as
  before an agent has started. plan_route records it on the job
  (``jobs.leader_agent``) with the agent that coded it (``jobs.coding_agent``),
  and a job's outcomes are credited from what it recorded, so a later edit
  of the row changes no past credit.

An agent's row says:

- ``roles``: what a job lets it do beyond its tools (#395). ``coder``
  (:data:`CODER`) runs it on the developer node: the job's branch, the pull
  request, salvage and the credit for the job; a route has at most one coder
  step. ``docs`` (:data:`DOCS`) lets a step that is no coder put files on the
  job's branch (triage's spec, the reviewer's notes, an audit's patch). Either
  makes the job's branch pushable from the first step that holds one. plan_route
  records each step's roles when it plans the job (:func:`step_roles`), so a
  later edit of the row changes no job planned;
- ``station``: where the guild hall draws it when it is home;
- ``quest_template``: whether the guild can give it a task, and what that
  task runs: NULL is no task, ``''`` a task that names no template (triage
  picks the route; a coding agent's only, the developer's), a name that
  template.

The pipelines the code defines (QA, onboarding, pull-request review) have
no template row: :data:`WORKFLOW_PIPELINES` says the same of them. Without a
database the built-in rows as they are seeded answer
(:func:`builtin_template`, :func:`builtin_agent`). A row read without one of
these columns (a fake, a summary from an older release) reads as its
column's default: the behaviour of a template or an agent of the
deployment's own before #167.

The built-in rows answer only where there is no database at all. Where a
read of the rows fails, nothing is known: no route is fixed, so triage
runs, and triage's pick of a template is not trusted (plan_route).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from embry0.agents.identity import DEFAULT_STATION, STATIONS, validate_station

#: The kinds a template may record its jobs as: ``orchestration.state.JobKind``.
JOB_KINDS: Final[tuple[str, ...]] = ("code", "research", "analysis", "ops")
#: The longest ``delivers`` a template may say: it is one line of a brief's prompt.
DELIVERS_MAX: Final = 300
#: The longest name a template or an agent column may hold (``pipeline_templates.name`` is at most 100).
NAME_MAX: Final = 100

#: The role that runs an agent on the developer node (#395): route_plan sends its step there, salvage reads its
#: output, and plan_route records the agent a job's route runs as its coder (``jobs.coding_agent``), whom its pull
#: request is credited to. An agent holds it on its row (``agent_definitions.roles``).
CODER: Final = "coder"
#: The role that lets a step that is no coder put files on the job's branch (#395): triage's spec or design, the
#: reviewer's notes, a context audit's checked patch.
DOCS: Final = "docs"
#: Every role an agent may hold; ``agent_definitions.roles`` is checked against the same list.
ROLES: Final[tuple[str, ...]] = (CODER, DOCS)
#: The roles that make the job's branch pushable, from the first step that holds one to the job's end.
PUSH_ROLES: Final[frozenset[str]] = frozenset({CODER, DOCS})
#: The coding agent of a route that names no other, and of a job that recorded none. It always holds
#: :data:`CODER`.
DEFAULT_CODER: Final = "developer"
#: The agent types of a route's review step and of its QA step, the graph's own vocabulary too: a job pinned to
#: the full pipeline (#306) keeps both whatever route a policy sends it to (:func:`full_pipeline_problem`).
REVIEW_AGENT_TYPES: Final[tuple[str, ...]] = ("reviewer", "review")
QA_AGENT_TYPE: Final = "qa"

#: Each column #167 added to ``pipeline_templates``, with what a row without it means.
TEMPLATE_DEFAULTS: Final[Mapping[str, Any]] = MappingProxyType(
    {"fixed_route": False, "offered_to_triage": True, "job_kind": None, "delivers": "", "leader_agent": None}
)
#: Each column #167 and #395 added to ``agent_definitions``, with what a row without it means.
AGENT_DEFAULTS: Final[Mapping[str, Any]] = MappingProxyType(
    {"station": DEFAULT_STATION, "quest_template": None, "roles": ()}
)

#: The pipelines the code defines, which have no template row: a job of one names it in
#: ``pipeline_template`` (``POST /jobs`` with ``pipeline``). What they lead with and deliver is the
#: code's, said here once for the world, progression and the job briefs.
WORKFLOW_PIPELINES: Final[Mapping[str, Mapping[str, Any]]] = MappingProxyType(
    {
        "qa": MappingProxyType({"name": "qa", "leader_agent": "qa", "delivers": "a test report"}),
        "pr-review": MappingProxyType(
            {"name": "pr-review", "leader_agent": "pr-reviewer", "delivers": "a review on the pull request"}
        ),
        "onboard": MappingProxyType(
            {"name": "onboard", "leader_agent": "onboarding", "delivers": "a test setup for the repository"}
        ),
    }
)


def _column(row: Mapping[str, Any] | None, key: str, defaults: Mapping[str, Any]) -> Any:
    value = (row or {}).get(key)
    return defaults[key] if value is None else value


# --- what a template's row says ---------------------------------------------------------------------------


def fixed_route(template: Mapping[str, Any] | None) -> bool:
    """Whether a job that names the template skips triage: its route is fixed."""
    return bool(_column(template, "fixed_route", TEMPLATE_DEFAULTS))


def offered_to_triage(template: Mapping[str, Any] | None) -> bool:
    """Whether triage's list of templates names it, and plan_route keeps triage's pick of it."""
    return bool(_column(template, "offered_to_triage", TEMPLATE_DEFAULTS))


def derived_kind(graph: Mapping[str, Any] | None) -> str | None:
    """The kind a route makes its jobs: ``"analysis"`` when every step but the output is a specialist (one of
    ``specialist_agents.SPECIALIST_AGENT_TYPES``, each of which examines a repository and reports), else None.
    Triage is the selector, not a step."""
    from embry0.workflows.issue_to_pr.specialist_agents import SPECIALIST_AGENT_TYPES

    nodes = (graph or {}).get("nodes") or []
    steps = [str(n.get("agent_type") or "") for n in nodes if n.get("agent_type") not in ("triage", "output")]
    if steps and all(step in SPECIALIST_AGENT_TYPES for step in steps):
        return "analysis"
    return None


def recorded_kind(template: Mapping[str, Any] | None) -> str | None:
    """The kind a job of the template is recorded as: the kind it is the default for, else its ``job_kind``,
    else the one its route makes it (:func:`derived_kind`), read from the row's graph as it is now."""
    row = template or {}
    said = str(row.get("default_for_kind") or row.get("job_kind") or "")
    return said or derived_kind(row.get("graph_definition"))


def delivers(template: Mapping[str, Any] | None) -> str:
    """What a job of the template hands back, in the person's words; '' when the row says nothing."""
    return str(_column(template, "delivers", TEMPLATE_DEFAULTS)).strip()


def leader(template: Mapping[str, Any] | None) -> str | None:
    """The character a job of the template is drawn as before an agent starts, and its outcomes' credit."""
    return str(_column(template, "leader_agent", TEMPLATE_DEFAULTS) or "") or None


def code_job_problem(template: Mapping[str, Any], coders: Collection[str]) -> str | None:
    """Why a code job cannot be sent to ``template`` by a policy (``slop_policy.route_template``); None when it can.

    The row says how a job of the template is recorded (:func:`recorded_kind`):
    one recorded as another kind, the research default or a route of
    specialists, would not run a code job as one. And its route needs a
    coding step, a step whose agent is one of ``coders`` (the agents with the
    :data:`CODER` role, :func:`coder_types`), or the job would change nothing. The
    words name the template, never a value of another key.
    """
    name = str(template.get("name") or "")
    kind = recorded_kind(template)
    if kind is not None and kind != "code":
        return f"{name!r} records its jobs as {kind} jobs, so a code job sent to it would not run as one"
    if not coder_steps(template.get("graph_definition"), coders):
        return f"{name!r} has no coding step (an agent with the {CODER} role), so a code job sent to it would change nothing"
    return None


def coder_steps(graph: Mapping[str, Any] | None, coders: Collection[str]) -> list[str]:
    """The node ids of ``graph``'s steps whose agent is one of ``coders``, in the graph's order."""
    return [
        str(node.get("node_id") or "")
        for node in (graph or {}).get("nodes") or []
        if str(node.get("agent_type") or "") in coders
    ]


def coder_steps_problem(name: str, graph: Mapping[str, Any] | None, coders: Collection[str]) -> str | None:
    """Why ``graph`` cannot be a route: it has more than one coder step (#395). The branch, its salvage, the review
    loop's return and the job's credit are one coder's; a reviewer sends work back to that one step."""
    steps = coder_steps(graph, coders)
    if len(steps) <= 1:
        return None
    return f"{name!r} has {len(steps)} steps whose agent has the {CODER} role ({', '.join(steps)}); a route has at most one"


def analysis_job_problem(template: Mapping[str, Any]) -> str | None:
    """Why a map's part cannot be analysed with ``template`` (``maps.analysis_template``); None when it can.

    A part sent for analysis runs as a read-only analysis job: the template must record its jobs as analysis jobs
    (:func:`recorded_kind`). The words name the template, never a value of another key.
    """
    name = str(template.get("name") or "")
    kind = recorded_kind(template)
    if kind is None:
        # A copy of analysis-default is one: the kind came from default_for_kind, which a copy does not take.
        return f"{name!r} does not say what kind of job it runs: set its job_kind to analysis"
    if kind != "analysis":
        return f"{name!r} records its jobs as {kind} jobs, so a part sent for analysis would not run as one"
    return None


def full_pipeline_problem(template: Mapping[str, Any]) -> str | None:
    """Why a job pinned to the full pipeline cannot be sent to ``template`` by a policy; None when it can.

    A job working a watcher-origin case keeps review and QA (#306,
    ``pin_full_pipeline``): a route a policy sends it to must have a review
    step (:data:`REVIEW_AGENT_TYPES`) and a QA step (:data:`QA_AGENT_TYPE`).
    """
    name = str(template.get("name") or "")
    steps = {str(node.get("agent_type") or "") for node in (template.get("graph_definition") or {}).get("nodes") or []}
    missing = [
        step
        for step, present in (("review", bool(steps & set(REVIEW_AGENT_TYPES))), ("QA", QA_AGENT_TYPE in steps))
        if not present
    ]
    if not missing:
        return None
    return f"{name!r} has no {' or '.join(missing)} step, and a job pinned to the full pipeline keeps review and QA"


# --- what an agent's row says ------------------------------------------------------------------------------


def roles(agent: Mapping[str, Any] | None) -> frozenset[str]:
    """The roles the agent's row holds, of :data:`ROLES`; none for a row without the column."""
    value = _column(agent, "roles", AGENT_DEFAULTS)
    if isinstance(value, str) or not isinstance(value, Iterable):
        return frozenset()
    return frozenset(str(role) for role in value) & frozenset(ROLES)


def is_coder(agent: Mapping[str, Any] | None) -> bool:
    """Whether the agent runs on the developer node: its row holds :data:`CODER`."""
    return CODER in roles(agent)


#: The graph's own steps that never run on the developer node, whatever a row says: the selector, the review, QA
#: and output steps have nodes of their own.
NEVER_CODERS: Final[frozenset[str]] = frozenset({"triage", "output", QA_AGENT_TYPE, *REVIEW_AGENT_TYPES})


def coder_types(agents: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The types of ``agents`` that hold :data:`CODER`; never one of :data:`NEVER_CODERS`."""
    return frozenset(str(agent.get("type") or "") for agent in agents if is_coder(agent)) - {""} - NEVER_CODERS


def roles_by_type(agents: Iterable[Mapping[str, Any]]) -> dict[str, frozenset[str]]:
    """The roles each agent a job can run holds, read from every row of ``agents``: each row that runs in jobs, and
    each built-in that has no row yet, as its seed says (a deployment's seeder writes the row when it starts). The
    graph's own non-coding steps never hold :data:`CODER` (:data:`NEVER_CODERS`); an agent that holds nothing is
    left out."""
    rows = list(agents)
    present = {str(agent.get("type") or "") for agent in rows}
    found: dict[str, frozenset[str]] = {}
    for agent in rows:
        if agent.get("runs_in_jobs", True) is not False:
            found[str(agent.get("type") or "")] = roles(agent)
    for name, held in builtin_roles().items():
        if name not in present:
            found[name] = held
    return {
        name: held - {CODER} if name in NEVER_CODERS else held
        for name, held in found.items()
        if name and (held - ({CODER} if name in NEVER_CODERS else set()))
    }


def coders_of(agents: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The agents a job can run on the developer node (:func:`roles_by_type`'s that hold :data:`CODER`)."""
    return frozenset(name for name, held in roles_by_type(agents).items() if CODER in held)


def builtin_roles() -> dict[str, frozenset[str]]:
    """The roles each built-in agent holds as the seeders write them; the ones that hold none left out."""
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    held = {name: roles({"type": name, **seed}) for name, seed in BUILTIN_SEED.items()}
    return {name: value for name, value in held.items() if value}


def builtin_coders() -> frozenset[str]:
    """The built-in agents that hold :data:`CODER` as the seeders write them: what answers without a database."""
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    return coder_types({"type": name, **seed} for name, seed in BUILTIN_SEED.items())


def step_roles(step: Mapping[str, Any]) -> frozenset[str]:
    """The roles a planned route step runs with: the ones plan_route recorded on it (``roles``).

    A step planned before roles were recorded (#395) has no ``roles`` key: the roles its built-in agent is seeded
    with answer, so a job paused mid-route resumes where it was. An agent of the deployment's own held none then.
    """
    if "roles" in step:
        recorded = step.get("roles")
        return roles({"roles": recorded if isinstance(recorded, list | tuple) else []})
    from embry0.workflows.issue_to_pr.route_plan import step_run_type

    agent_type = step_run_type(str(step.get("agent_type") or ""))
    return builtin_roles().get(agent_type, frozenset()) - ({CODER} if agent_type in NEVER_CODERS else frozenset())


def is_coder_step(step: Mapping[str, Any]) -> bool:
    """Whether a planned route step runs on the developer node: it holds :data:`CODER`."""
    return CODER in step_roles(step)


def station(agent: Mapping[str, Any] | None) -> str:
    """Where the guild hall draws the agent when it is home; the desk for a row that names no known station."""
    value = str(_column(agent, "station", AGENT_DEFAULTS))
    return value if value in STATIONS else DEFAULT_STATION


@dataclass(frozen=True)
class Quest:
    """Whether the guild can give an agent a task, and the template that task names (None: triage picks)."""

    offered: bool
    template: str | None


def quest(agent: Mapping[str, Any] | None, templates: Collection[str] | None = None) -> Quest:
    """The guild's task for the agent, from its ``quest_template``.

    With ``templates`` (the names that exist), a row naming a template that
    no longer exists offers no task: a task would run the kind default
    instead of what the operator chose.
    """
    value = (agent or {}).get("quest_template")
    if value is None:
        return Quest(False, None)
    name = str(value).strip() or None
    if name is not None and templates is not None and name not in templates:
        return Quest(False, None)
    return Quest(True, name)


# --- checking a value an operator writes -------------------------------------------------------------------


def checked_job_kind(value: Any) -> str | None:
    """``job_kind`` as stored: None, or one of :data:`JOB_KINDS`. ValueError naming the field otherwise."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in JOB_KINDS:
        raise ValueError(f"job_kind must be one of {', '.join(JOB_KINDS)} or null; got {value!r}")
    return value


def checked_delivers(value: Any) -> str:
    """``delivers`` as stored: one line of at most :data:`DELIVERS_MAX` characters, trimmed."""
    if not isinstance(value, str):
        raise ValueError(f"delivers must be text; got {type(value).__name__}")
    text = value.strip()
    if len(text) > DELIVERS_MAX:
        raise ValueError(f"delivers must be at most {DELIVERS_MAX} characters; got {len(text)}")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("delivers must be one line of text")
    return text


def _checked_name(field: str, value: Any, *, empty_ok: bool) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a name or null; got {type(value).__name__}")
    name = value.strip()
    if not name and not empty_ok:
        raise ValueError(f"{field} must be a name or null; got ''")
    if len(name) > NAME_MAX:
        raise ValueError(f"{field} must be at most {NAME_MAX} characters; got {len(name)}")
    return name


def checked_leader_agent(value: Any) -> str | None:
    """``leader_agent`` as stored: None or an agent type's shape. Whether the agent exists is the API's check."""
    return _checked_name("leader_agent", value, empty_ok=False)


def checked_quest_template(value: Any) -> str | None:
    """``quest_template`` as stored: None, '' (the task names no template) or a template's name."""
    return _checked_name("quest_template", value, empty_ok=True)


def checked_roles(value: Any) -> list[str]:
    """``roles`` as stored: a list of :data:`ROLES`, each once, in that order. ValueError naming the field
    otherwise."""
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ValueError(f"roles must be a list of {', '.join(ROLES)}; got {value!r}")
    unknown = [str(role) for role in value if role not in ROLES]
    if unknown:
        raise ValueError(f"roles must be a list of {', '.join(ROLES)}; got {', '.join(map(repr, unknown))}")
    return [role for role in ROLES if role in value]


def checked_station(value: Any) -> str:
    """``station`` as stored: one of the guild hall's stations (``identity.STATIONS``)."""
    if not isinstance(value, str):
        raise ValueError(f"station must be one of {', '.join(STATIONS)}; got {value!r}")
    return validate_station(value)


def leader_problem(value: str | None, agent_types: Collection[str] | None) -> str | None:
    """Why ``leader_agent`` cannot be saved: it names no agent a job runs. None when it can, or when the
    agents are unknown (no database)."""
    if value is None or agent_types is None or value in agent_types:
        return None
    return f"leader_agent: no agent named {value!r} runs in jobs"


def untemplated_task_problem(agent_type: str, value: str | None, *, coder: bool) -> str | None:
    """Why ``agent_type`` cannot take a task that names no template: such a task runs the route triage picks,
    whose coding step is a coder's, so it is only an agent's with the :data:`CODER` role (``coder``)."""
    if value != "" or coder:
        return None
    return (
        "quest_template: '' (a task that names no template) runs the route triage picks, which only an agent with "
        f"the {CODER} role works on; name a template for {agent_type!r}"
    )


def template_name_problem(name: str | None) -> str | None:
    """Why a template cannot be named ``name``: it is the name of a pipeline the code defines, which a job's
    ``pipeline_template`` already means (:data:`WORKFLOW_PIPELINES`)."""
    if name is None or name.strip() not in WORKFLOW_PIPELINES:
        return None
    return (
        f"name: {name.strip()!r} is a pipeline embry0 defines ({', '.join(WORKFLOW_PIPELINES)}); "
        "a template cannot take its name"
    )


def quest_template_problem(value: str | None, template_names: Collection[str] | None) -> str | None:
    """Why ``quest_template`` cannot be saved: it names no template. None when it can, or when the templates
    are unknown (no database)."""
    if not value or template_names is None or value in template_names:
        return None
    return f"quest_template: no pipeline template named {value!r}"


# --- rows ---------------------------------------------------------------------------------------------------


def pipelines_by_name(templates: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Every pipeline a job's ``pipeline_template`` can name, by name: the template rows and the code's own."""
    rows: dict[str, Mapping[str, Any]] = dict(WORKFLOW_PIPELINES)
    for row in templates:
        if row.get("name"):
            rows[str(row["name"])] = row
    return rows


def builtin_template(name: str | None) -> dict[str, Any] | None:
    """The built-in template ``name`` as the seeder writes it, or a pipeline the code defines; None for others.

    What answers without a database: the rows every deployment has. Never a
    stand-in for a read that failed.
    """
    from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES

    if not name:
        return None
    seed = BUILTIN_PIPELINE_TEMPLATES.get(name)
    if seed is not None:
        return {"name": name, **seed}
    workflow = WORKFLOW_PIPELINES.get(name)
    return dict(workflow) if workflow is not None else None


def builtin_templates() -> list[dict[str, Any]]:
    """Every built-in template as the seeder writes it."""
    from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES

    return [{"name": name, **seed} for name, seed in BUILTIN_PIPELINE_TEMPLATES.items()]


def builtin_agent(agent_type: str | None) -> dict[str, Any] | None:
    """The built-in agent ``agent_type`` as the seeders write it; None for an agent of the deployment's own."""
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    seed = BUILTIN_SEED.get(agent_type or "")
    return {"type": agent_type, **seed} if seed is not None else None

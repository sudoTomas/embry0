# Knowledge maps: groups, and their labels and template from settings (#180)

Status: decided (2026-11-23). Pull request 1 builds section 2, the format's groups; pull request 2 builds section 3,
the settings. Refs #159, #175, #180. Line numbers are at `cc117333`.

## 1. What is left of #180

- **Owners: done.** Each map has had an owner since migration 133: the key is `(slug, owner)`, and a missing owner
  means admin-only. PUT, DELETE, marks and sends check the caller's organization.
- **Two things remain:**
  - **A third meaning of "areas".** The format `embry0.map/1` calls the scanner's rings of parts `areas`. A
    repository's areas (spec 2026-11-10) and qa.yaml's apps already use that word, and the map page already calls
    the rings groups.
  - **Literals for policy.** These are hard-coded:
    - the labels on a map's issues: `embry0-map` (`embry0/api/v1/knowledge_maps.py:693`);
    - the template of a part sent for analysis: `analysis-default` (`:712`);
    - the labels on a lead's issue: `embry0-lead` (`:768`, `embry0/api/v1/leads.py:227`).

## 2. Groups (pull request 1)

- **The format reads `groups`, and keeps reading `areas` as an alias.**
  - The two are one list under two names. A document that gives both is refused with 422, since one of them
    would be dropped unseen.
  - Either must be a list.
  - `embry0.map/1` keeps its version: a scanner that writes `areas` goes on working.
- **The viewer's payload serves `groups`, and `areas` too, for one release; each part likewise.**
  - `parse_map` stores both names.
  - Migration 153 copies each stored map's `areas` list in as `groups`. It works by text alone, because a stored
    graph may be text Postgres's JSONB refuses (NaN, a NUL, a lone surrogate) while the viewer's JSON reader reads
    it.
  - This is safe because every stored graph was written by `parse_map` as compact JSON, its strings escaped.
    `"areas":` there can only begin the list and `,"nodes":` can only end it.
  - A graph with no `areas` list, or with `groups` already, is left as it is.
- **The dashboard reads `groups`.** `mapGroups(graph)` returns `groups`, else `areas`, so a dashboard that reaches a
  backend from before this release still draws the rings.
- **Docs.** `docs/maps.md` documents `groups`, and `areas` as its old name; `docs/api.md` names both in the
  payload.
- **The next release** stops serving `areas`: `parse_map` stops storing it, and a migration takes it out of stored
  graphs. Reading `areas` in an upload stays, as the alias of `embry0.map/1`.
- **A part names its group as `group`, by the same rule.** A part's `area`, its name before, is read as the same
  field, and a part that gives both is refused with 422. The stored part holds both names for one release.
  - Migration 153 gives each stored part its `group` by text too. In a stored graph `,"area":` only begins a part's
    ring, which is `null` or a string up to `,"label":`, so one replace with a regular expression copies it in.
  - The dashboard reads `nodeGroup(node)`: `group`, else `area`.

## 3. Labels and template from settings (pull request 2)

- **A new `maps` section.** An organization's layer and a repository's may set both of its keys:
  - `analysis_template` (default `analysis-default`): the template a part sent for analysis runs. It is checked
    against the deployment's templates the way `slop_policy.route_template` is (`slop_policy.py:152-163`,
    `:228-250`): a write naming a template the deployment does not have, or one that does not record its jobs as
    analysis jobs, is refused. While a settings document names a template, deleting it, renaming it or changing
    it so that it stops recording analysis jobs is refused with 409 naming the documents, as
    `services/slop_routing.py` does for a route template; the write reads the templates `FOR SHARE`, so the two
    cannot race.
  - `labels` (default `["embry0-map"]`): the labels on the issue a map's send opens, beside `map:<slug>`.
- **`leads.labels`** (default `["embry0-lead"]`) goes in the `leads` section: the labels on a lead's issue, beside
  `lead:<kind>`. Like the other leads keys, an organization's layer may set it.
- **No label starts a job.** An issue embry0 opens runs as a job already; were it labelled with a trigger label, a
  forge's webhook would start it again. The trigger labels are the deployment's alone: the
  `integration_config.trigger_labels` row, else `TRIGGER_LABELS`, else `embry0`, read where the webhook reads them
  and compared as it compares them, stripped and case-folded. No organization or repository sets its own.
  - **At the write.** A write of `maps.labels` or `leads.labels` naming a trigger label is refused with 422. The
    location names the label's place in the list (`["maps", "labels", 1]`) and the message does not repeat it.
    A label the document held before the write is not judged again, so a write of another key, or of the same
    document, still goes through.
  - **When the trigger labels change later.** A change of the trigger labels is not refused: one comes from the
    environment at a restart, where nothing can refuse it, and refusing it at `PUT /config/integrations` would
    leave the environment's path open. The fail-safe is at use: each label an issue is about to get, a
    section's and the keyed ones alike, is checked against the trigger labels as they are then, and one that is
    a trigger label is left off the issue and logged as `issue_label_skipped`. When the trigger labels cannot be
    read, every label a webhook could fall back to is left off.
- **What stays in code.** `map:<slug>`, `lead:<kind>` and `SOURCE_PREFIX` stay as they are: they key embry0's own
  records, and a deployment that changed them would orphan its open leads and sends.
- **Readers.** Each reader takes its values from the settings of the repository the issue is opened in, as the
  layers resolve them: `knowledge_maps.py:693`, `:712` and `:768`, and `leads.py:227`. A store that cannot be
  read gives the defaults, logged, so an issue is still opened.
- **Coordination.** #650 and its follow-up also change `embry0/settings/sections/leads.py`, so pull request 2
  starts from `main` after #650 merges, or rebases onto it.

## 4. Acceptance (pull request 1)

| Check | How |
|---|---|
| `groups` is read, and `areas` as its alias | `parse_map` given either name serves the list as `groups` and `areas` (`tests/unit/services/test_knowledge_maps.py`). |
| Both names together are refused | `parse_map` raises `MapError`, and the upload answers 422 (`tests/unit/api/v1/test_knowledge_maps_api.py`). |
| The viewer is served `groups` | `GET /maps/{slug}` after an upload naming `groups` carries `groups` and `areas`. |
| A map stored before is served `groups` | Migration 153 gives a stored graph `groups`, also one JSONB refuses, and leaves a graph with no `areas` or with `groups` as it was (`tests/unit/storage/test_migration_153.py`). |
| A part names its group as `group`, or `area` | `parse_map` given either serves both; both on one part are refused; a stored part gets its `group` from migration 153, even one whose strings hold `,"area":` and `"label":`. |
| The dashboard draws either | `mapGroups` reads `groups`, else `areas`, and `nodeGroup` a part's `group`, else `area` (`frontend/src/api/__tests__/maps.test.ts`). |

## 5. Out of scope

- Any change to who may change a map, or to its owner.
- A version bump of the format.


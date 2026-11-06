# Slop routing

Triage and the watcher judge each code job and each finding: a **bug** (the behavior differs from what is intended), **slop** (the behavior is what the code says, and the trouble is its shape) or **noise**. A developer handed slop as a bug patches the symptom and adds more slop, so a code job judged slop runs a cleanup instead: by default the `slop-cleanup` template, where the Slopmaster keeps behavior and removes or merges code, and the reviewer checks that it did ([the design](superpowers/specs/2026-10-25-slop-dungeon-design.md)). The [Slop-O-Meter](slop-meter.md)'s reading tips a borderline verdict toward slop, and gives an area a Slop Dungeon on the [world](world.md) map.

What that verdict sets in motion is the settings document's `slop_policy` section ([settings document](configuration.md#settings-document); every key with its bounds is in the [reference](configuration.md#slop_policy)). Each default is what the code held before the section, so a deployment that writes nothing routes, prompts and files tickets exactly as before.

## Settings

| Key | Default | What it does | Layers |
|---|---|---|---|
| `enabled` | `true` | A code job judged slop runs `route_template`. `false`: it takes its normal route (triage's pick, else its kind's default); the verdict is still recorded on the job, and triage is told the verdict changes nothing | organization, repository |
| `route_template` | `slop-cleanup` | The pipeline template a code job judged slop runs. A write that changes it must leave a template that takes a code job ([below](#the-route-template)). A job pinned to the full pipeline keeps review and QA ([below](#a-job-pinned-to-the-full-pipeline)) | organization, repository |
| `min_score_to_route` | `0` | The least `slop_score` (0 to 100: how much of the trouble is shape) a slop verdict needs to be routed; below it the job takes its normal route. `0` routes every slop verdict | organization, repository |
| `kinds` | the seven below | The kinds of slop a verdict may name, as lowercase words joined by `_`. A kind a classifier names that is not listed is dropped from its verdict. At least one, at most 20 | organization |
| `classifier_guide` | the text below | What triage and the watcher are told slop, a bug and noise are, and what `slop_score` means. It is part of their system prompts; describe each of `kinds` in it. At most 4,000 characters | organization |
| `review_guide` | the text below | What the reviewer of the Slopmaster's change is told to check, before its usual instructions; the lines the branch adds and removes follow it. At most 2,000 characters | organization |
| `ticket_heading` | `Slop, not a bug` | The heading the watcher's draft ticket for a slop finding opens with, in the organization's tracker, written without its `#` marks. Triage is told to weigh a section under it | organization |
| `triage_lean_min_level` | `4` (Sloppy Joe) | The Slop-O-Meter level from which triage leans toward slop on a borderline verdict. Triage is told the levels by the names the meter gives them (`slop_meter.level_names`), so renamed levels read the same in both. `null`: triage is given the reading and never told to lean | organization |
| `dungeon_min_level` | `2` (Spaghetti Bolognese) | The Slop-O-Meter level from which a repository or an area has a Slop Dungeon on the world map even with no open slop finding. `null`: only open slop and cleanups merged lately make one | organization |

The default kinds are `duplication`, `dead_code`, `half_wired`, `contradicting_layers`, `symptom_patch`, `misleading_docs` and `off_pattern`, each described in the default `classifier_guide`. The default texts are in the [example settings file](examples/settings.yaml) (`embry0 settings export --default` prints it).

Examples:

```yaml
# The deployment's document: route to an operator's own cleanup, and only sure verdicts.
slop_policy:
  route_template: tidy-up
  min_score_to_route: 60
```

```yaml
# PUT /api/v1/config/settings?owner=acme: acme's own kinds, words and ticket heading.
slop_policy:
  kinds: [duplication, dead_code, naming]
  classifier_guide: |-
    Tell slop apart from bugs:
    - bug: the behavior differs from what the code, its docs or the ticket intend.
    - slop: the behavior is intended, and the shape is the trouble. Kinds:
      duplication (one logic in two places), dead_code (nothing reaches it),
      naming (a name that says something the code does not do).
    - noise: nothing to do.
    slop_score (0-100) is how much of the trouble is shape rather than behavior.
  ticket_heading: Shape, not behaviour
  triage_lean_min_level: 3
```

```yaml
# PATCH /api/v1/config/settings/slop_policy?repo=acme/legacy {"enabled": false}: this repository keeps its routes.
slop_policy:
  enabled: false
```

Experience for a merged cleanup is the [progression pack](progression.md)'s `outcomes.cleaned` (a base, plus some per net line removed, up to a maximum), which an operator already sets there; it is not a key of this section. No environment variable ever set any of these keys, so nothing is imported at the first start and no variable is retired.

## Layers

Whether, where and how surely a job is routed are an organization's and a repository's to say, a repository's layer over its organization's, as for the background features ([which layer wins](configuration.md#settings-document)): a person can already name the pipeline of any job of a repository. The rest is an organization's own definition of slop, the bar its cleanups are reviewed against and how its tickets read. Those words go into agents' system prompts, so a repository's layer, which a key with a repository allowlist may write, holds none of them, and an organization's layer reaches its own repositories' jobs only.

Two things follow from that order:

- A repository's layer that sets `enabled: true` routes that repository's jobs judged slop even when its organization's layer sets `enabled: false`.
- A repository's layer that sets `route_template` holds that template against a delete or a rename ([below](#the-route-template)) until someone changes or takes out the key in that layer.

## When it is read

- **A job** reads the section for its repository once, when it starts, and keeps it to its end, a resume included, as it does `done_when` and `salvage`: triage's prompt, the route and the reviewer's guide come from what it read. A change reaches the next job that starts.
- **The watcher** reads its stack's repository's section for each analysis (the guide and the kinds) and for each draft it files (the heading), with no restart.
- **The world** reads each repository's `dungeon_min_level` each time it draws the dungeons.

Triage's prompt is built from the section, with the default texts giving the prompt it held before to the byte. It is told that a slop verdict routes the job to the Slopmaster's cleanup when `route_template` is the built-in `slop-cleanup`, whose graph is written again at every start, and to the cleanup pipeline it names otherwise, whichever agent codes it. Each part is filled in one pass, so a guide that contains a marker such as `<<MODEL_GUIDANCE>>` is sent as written and never filled in.

## The route template

A write that changes the template a document's readers route to answers 422 naming `slop_policy.route_template` unless that template is one that takes a code job. That is a write naming another template, and also one that takes the key, or the whole section, out of the last document that set it, so its readers fall back to what they inherit or to the default `slop-cleanup`. Every write path is checked the same way: `PUT` and `PATCH /api/v1/config/settings` and their dry runs, `embry0 settings apply` and `set`, MCP `settings_set` and `settings_set_keys`, the Policy tab, and an undo. A template takes a code job when:

- the template exists (`GET /api/v1/pipelines/templates` lists them; the pipelines the code defines, `qa`, `pr-review` and `onboard`, are not templates);
- its jobs are recorded as code jobs: it is the default of no other kind, says no other `job_kind`, and is not a route of specialists only, which is analysis (the template's row, [api.md](api.md#pipeline-templates));
- its route has a coding step, `developer` or `slopmaster`.

Only a template the write changes is checked, so a write of another key never trips over a template its readers already routed to, even one deleted since by hand (jobs then take their kind's default route, below). A layer that does not set `route_template` inherits the one above it.

**While a document routes to a template, it stays one that can be routed to.** `DELETE /api/v1/pipelines/templates/{id}`, and a `PUT` that renames the template or changes it so that it no longer takes a code job, answer 409 naming each document that routes to it: the deployment's (`its default` when it sets no `route_template`, which names `slop-cleanup`), and each organization's or repository's layer that sets it. Point those documents at another template first; that write is recorded and can be undone like any other. Turning the policy off does not release the template: a document that names a template keeps it, so turning the policy on again does not route to a template deleted while it was off.

- A rename does not rewrite the documents, as it moves an agent's `quest_template`: a document is the operator's text, possibly kept in a file that the next `embry0 settings apply` writes back.
- The check and the change cannot race: a settings write that may change `slop_policy` reads the templates `FOR SHARE` in its transaction (a write of any other section reads none and locks nothing), and a template's change locks its row `FOR UPDATE` before it reads the documents, so whichever comes second sees what the first committed.
- A template that is gone anyway, invalid, or no longer takes a code job (its row edited in the database by hand) is logged when a job would run it (`plan_route_template_not_found` with `slop_route`, `plan_route_template_invalid`, `plan_route_slop_template_unfit`), and the job takes the route its kind defaults to. A slop route never fails a job, unlike a template a person named that is invalid.

### A job pinned to the full pipeline

A job working a watcher-origin case is pinned to the full pipeline (`pin_full_pipeline`): it keeps review and QA. When it is judged slop, it takes `route_template` only when that template's route also has a review step (`reviewer` or `review`) and a QA step (`qa`), as `slop-cleanup` does; otherwise it is logged (`plan_route_slop_template_unfit`) and takes the code default, as for any template it cannot take. A job that is not pinned takes a lighter cleanup route, such as `quick-fix`, as its policy says.

### The built-in templates

The built-in templates, `slop-cleanup` among them, are written again at every start, as before: their graph and description are embry0's, while what their rows say about their jobs stays the operator's. Since routing is now `enabled` and `route_template`, deleting `slop-cleanup` is no longer how to stop routing, and a deployment that points `route_template` elsewhere leaves it unused.

## What the routing logs

- `plan_route_slop_cleanup`: a code job judged slop runs `template`, with its `slop_score`.
- `plan_route_slop_not_routed`: a code job judged slop keeps its route, with the `reason` (`slop_policy.enabled is false`, or a score below `min_score_to_route`).
- `pipeline_template_change_refused`: a template change refused, with the documents that route to it.
- `watcher_slop_policy_unread`: the watcher could not read its stack's policy and used the defaults for that analysis.

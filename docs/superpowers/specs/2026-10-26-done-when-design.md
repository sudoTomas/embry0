# Done when: the finish line a coding job works to

**Why.** No agent was told when it was done. Triage wrote acceptance criteria only when a job needed QA. Review read a ticket's `## Acceptance criteria` section, or was told to "derive what done means from the task". The developer's steps ended at "push the branch", plus a reminder of its turn budget. Some tickets have no finish line of their own, such as a "diagnose …" ticket backed by thousands of characters of log evidence. There, a developer can spend its whole turn budget reading and searching, and stop at the cap having committed nothing.

## The list

A code job carries a **done-when list**: `jobs.done_when` (migration 120) and `state["done_when"]`.

```json
{
  "items": [{"id": "d1", "done": "<a condition>", "shown_by": "<how it is shown>"}],
  "out_of_scope": ["<what this job does not do>"],
  "source": "triage" | "ticket" | "person",
  "edited_by": null, "edited_at": null,
  "report": null
}
```

- **The items.** There are 2–5 of them (at most 6 are kept), each with how it is shown: a test passes, a command prints something, a log line appears, a document says something, or a written diagnosis names the cause with its evidence.
- **Out of scope.** What the job must not take on.
- **A diagnose or investigate ticket** ends in a deliverable that can be reached, for example "a written cause with evidence in the pull request description, or 'cannot tell', naming what is missing". It never ends in open-ended investigation.

## Who writes it

- **Triage** drafts it for every code job, as `done_when` in its JSON. When the ticket has a `## Acceptance criteria` section, those criteria are the base. `plan_route` stores the list when the job runs the code route.
- **The ticket.** If triage gives no usable list, the ticket's acceptance-criteria bullets become the list (`source: "ticket"`). With neither, the job has no list and behaves as before.
- **A person** can replace it with `PUT /api/v1/jobs/{job_id}/done-when` (`{"items": [{"done", "shown_by"}], "out_of_scope"}`), audited as `job.done_when_edited`, while the job is pending, running, paused or awaiting input. An edit drops the old report. The developer node reads the job row at the start of every run, so the next run works to the edited list.

## The developer's contract, with pacing

- **The prompt.** The developer, or the Slopmaster on a cleanup route, gets the list as its contract in its prompt, on a fresh run and on a resumed one.
- **Pacing.** The turn-budget note names the turns:
  - by half its budget, it commits what it has;
  - by three quarters, it stops exploring and finishes against the list;
  - once every item is met, it commits, pushes and stops.
- **The report.** Its final JSON carries `"done_when": [{"id": "d1", "met": true|false, "evidence": "<what shows it>"}]`.

## The check

After a coding run that pushed its branch, embry0 reads that report strictly:

- **It counts as met** only when it names a listed `id`, has `met: true` and non-empty `evidence`.
- **It counts as unmet** when it is `met: false`, unreported, or has no evidence. A run with no report at all leaves every item unmet.

What happens next:

- **Everything met.** The job moves on: review, then QA.
- **Something unmet, first time.** The coding step runs once more (`developer` → `developer`), with the unmet items named.
- **Still unmet after that.** The job pauses (`max_retries`, stage `done_when_unmet`), and the pause names the unmet items.

The report is saved on the job (`done_when.report`), so the job page shows each item as met or unmet.

## Review and QA

- **Review** judges the change against the list and the developer's report, in place of the ticket's own criteria section. Review decides; the report is the developer's claim, not proof.
- **QA** adds the list's items to each app's acceptance criteria when triage named none of its own. The app's own criteria from `qa.yaml` stay.

## The job page

The brief card lists the items under "Done when", with how each is shown and, once a report exists, whether it was met. The out-of-scope items sit under a fold. An **Edit** control opens the list, one item per line as `condition — how it is shown`, and saves it with the endpoint above.

## Not covered

Pacing is an instruction plus the turn-budget note. Nothing stops a run in the middle at half its turns. The work of a run that stops at the cap is kept by the commit-on-stop path (salvage), not by this change.

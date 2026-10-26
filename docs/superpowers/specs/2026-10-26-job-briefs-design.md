# Job briefs: what a job is, why it matters, and what is happening now, in plain words

**Why.** A job's page showed its task cut to three lines, e.g. "[ENG-3075] [watcher] Diagnose the boot crash-loop that 502s both copies ## Evidence Window …", and the lists showed the same first line. The task is written for the agents: tracker tags, log evidence, instructions, often thousands of characters. Someone opening a job could not tell what it was about, why it mattered, or what was happening to it. Jobs can stay complicated inside; what a person sees has to be quick to understand.

## The brief

One per job, written by a model on the [proposal briefs](2026-10-25-proposal-briefs-design.md) machinery (the same endpoint resolution, the same model call with the answer's schema enforced while a vLLM-style server decodes it and thinking off, strict parsing):

```json
{"title": "…", "what": "…", "why": "…", "done": "…", "size": "small|medium|large"}
```

- `title`: a plain, specific name for the job, phrased as what it does, at most 70 characters; no tags, keys, ids or prefixes.
- `what`: what happened or what is asked, concretely, as far as the task says.
- `why`: who or what it affects, and what happens if nobody does it; "The task does not say why it matters." when it does not.
- `done`: what finished looks like, ending with what the person gets back ("You get a pull request.").
- `size`: how much work it is.

**What it is written from.** The task's first 6,000 characters, and what embry0 knows about the job:
- the repository and area;
- the job's kind and template, translated to what the job delivers (a pull request, a written report, an analysis of the production logs and a ticket when it finds a problem worth one, …);
- its issue's title;
- the slopometer's verdict (`jobs.work_nature`) when there is one.

The prompt says to use only what the task says and to leave out ids and log lines. Tags (`[…]`), job ids, tracker keys and proposal ids left in the answer are taken out, not refused: a refusal would only retry the same answer.

**Where it is kept.** `job_briefs` (migration 119): `job_id` (key), `input_hash`, `title`, `what`, `why`, `done`, `size`, `model`, `written_at`. The hash covers the inputs above and `PROMPT_VERSION`, so a changed task, a new verdict or a new prompt gets a new brief. A brief is shown only while its hash matches the job as it is now.

**The writer.** `JobBriefWriter` runs every `JOB_BRIEF_INTERVAL_SECONDS` (60) and writes at most `JOB_BRIEFS_PER_TICK` (3) a tick:
- **Order:** live jobs first (pending, running, paused, awaiting input), then jobs finished in the last 7 days, newest first.
- **Model:** `JOB_BRIEF_MODEL`, else the proposal brief model, else the watcher's local-first model. With none, nothing is written.
- **Failures:** a failed job waits 30 minutes before it is tried again.
- **Never in the way:** nothing waits on a brief. A job without one shows its task's first line.

## Now: the status in plain words

`status_words` is worked out by the API with no model, from the job's status, `current_stage`, its latest `interrupt` event (reason, summary, options, and `budget` when a pause is over the job's budget), `account_wait`, and its error. It is `{state, headline, next, actions, until}`, so every surface says the same thing:

| The job | Headline (and next) |
|---|---|
| pending | Queued: it starts when a run slot is free. |
| running | its stage as what is happening ("Working: the developer is writing the change."; "Running: <stage>." for a stage without words) |
| paused on a spent account | Waiting: the <provider> account is <reason>; it goes on by itself when the account is ready. Next "Try now or cancel."; `until` the reset |
| paused over its budget | Paused: it went over its budget, $spent of $cap. Next "Continue (+$continue_adds) or stop." |
| paused at max retries | Paused: <why, by the stage it came from> (the developer ran out of turns and its work is saved; stopped without pushing; the review failed or gave no verdict; no toolchain; changes asked after every retry), else the summary's first sentence. Next: the pause's options in words |
| awaiting input | Waiting for your answer: <its first question>. Next "Answer it below." |
| finished | Done: a pull request is open for review / was merged / was closed without merging / its report is ready; Partly done; Skipped |
| failed | Failed: <the failure in plain words, as the guild says it>. Next "Run it again, or read the log for the cause." |

## API

- `GET /jobs/{id}`: `brief` (`{title, what, why, done, size, model, written_at}`, or `null` until written) and `status_words`.
- `GET /jobs`: each row's `brief_title`, left out until the brief is written, so a list page stays as small as before.
- The world: a party's, an inbox card's, a loot entry's, a quest log entry's and the quest scroll's `title` is the brief's title when there is one, else the task's first line as before.

## The page

The job page leads with the brief card:
- the title, then repository · area · size;
- **What**, **Why**, **Done** and **Now** (the status headline and what a person can do, with a wait's end in the viewer's time);
- the full task one click away, rendered as markdown.

The jobs table and the console cards name each job by its brief title.

## Not in this

The owner proposals' "Started" answer still names the job by its id. A proposal shows no linked job's title anywhere else.

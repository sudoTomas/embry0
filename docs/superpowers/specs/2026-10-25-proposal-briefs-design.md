# Proposal briefs: what is wrong and how embry0 would fix it, in plain words

**Why.** An owner proposal carries a `summary` written for embry0, not for its owner: "A backlog ticket embry0 could take (40/100 ready: a full description, says what done looks like): ops(gpu): …", or a context finding's `AGENTS.md:34: "…"`. An owner reading a chat message cannot say yes or no from that without opening the ticket, and the proposal's id says nothing to a person. The owner needs, for each proposal: what the problem is, and what embry0 would do about it.

## The brief

One per open proposal, written by a model from what embry0 already holds about it (kind, repository, title, details, what accepting does, files, severity), never from anything else:

```json
{"title": "…", "problem": "…", "fix": "…", "size": "small|medium|large"}
```

- `title`: a plain name for the work, at most 70 characters; no conventional-commit prefix, no ticket key, no id.
- `problem`: one or two sentences: what is wrong or missing, and who or what it affects.
- `fix`: one or two sentences: what embry0's agents would do, and what the owner gets back (a pull request, a report, a ticket update).
- `size`: how big the work is.

The prompt forbids ids, file paths unless the fix is about that file, and anything the proposal does not say. The answer is parsed strictly: a JSON object with exactly those four keys, strings of bounded length, `size` one of three words, and no proposal id in the text. Anything else is rejected and tried again later; a long title is cut at a word.

## Where it is written

- **Table** `proposal_briefs` (migration 117): `proposal_id` (key), `input_hash`, `title`, `problem`, `fix`, `size`, `model`, `written_at`.
- **The input hash** covers the proposal's kind, repository, title, details, action, files and severity, with every run of digits folded to one mark, so a watcher case whose count goes up keeps its brief and a ticket whose description changes gets a new one.
- **The writer** (`ProposalBriefWriter`, a background loop) runs every `PROPOSAL_BRIEF_INTERVAL_SECONDS` (60) and writes at most `PROPOSAL_BRIEFS_PER_TICK` (3) briefs a tick: missing or stale ones of the proposals still asked, watcher cases first, then leads, issues and findings, newest first; held tickets last. A failed write waits 30 minutes before the same proposal is tried again. Briefs of proposals no longer asked are deleted.
- **The model** is `PROPOSAL_BRIEF_MODEL`, else the watcher's local-first model (`provider_config.watcher_local_model`): a deployment that runs a local model writes briefs on it, at no cost per token. Empty both, no briefs are written and the feed stays as it was. Only models of the OpenAI-compatible family are called (`POST <root>/chat/completions`, the key and root the credential resolver gives a job with no repository binding). For the custom provider the request asks for a JSON object and turns the model's thinking off (`chat_template_kwargs.enable_thinking=false`): a brief is a short task.
- **Never in the way.** The feed reads stored briefs only; it never calls a model. A proposal without a brief, or whose brief is stale, has `brief: null` until the writer gets to it.

## The readiness floor

A backlog ticket (`ticket` lead) is asked only at readiness `PROPOSALS_TICKET_MIN_READINESS` (60) and up. One below it is **held**: left out of `GET /proposals` (which an assistant reads to message owners) unless `include_held=true`, which the Owner proposals page sends, showing held ones folded under "Not ready enough to ask". A held proposal can still be read, accepted, declined or snoozed by id.

## API

- `GET /proposals` and `GET /proposals/{id}`: each proposal adds `brief` (`{title, problem, fix, size, model, written_at}` or `null`) and `held` (bool). `GET /proposals` takes `include_held` (default false).
- The MCP proposal tools pass the same through; `proposal_list` tells the assistant to message an owner with the brief when there is one.

## The page

The Owner proposals page leads each card with the brief: its title, **Problem**, **Fix** and size. The raw summary shows when there is no brief yet. The id stays out of the text; the details expander keeps the summary, the evidence and the id.

## Not in this

The chat assistant's message (numbering, three at a time, answering by number) lives with the assistant, outside embry0.

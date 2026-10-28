# Decisions: one card for every choice a person makes, sent to the right person

**Goal.** A person decides two kinds of thing in embry0: whether to start work embry0 found (a quest on offer: a watcher case, an issue, an audit finding, a lead) and what to do with work that is done (merge it, send it back, reject it). Today each lives on its own screen in its own words, the facts that decide it are buried in long reports, the decision cannot be handed to the teammate who knows the code best, and the chat path that asks owners depends on a model reading replies like "yes 2". This spec gives every such choice one shape, a **decision**: the same card on every screen and in a chat, a **hand-off** to send it to someone else, and an **ask** that tracks it from sent to answered, with no model in between.

**Non-goals.** No chat client in embry0: the deployment's assistant renders and sends (as owner proposals already work). No change to what accepting, merging or rejecting does. API paths, database tables and MCP tool names keep their names.

## Words

Everything a person reads calls a piece of work a **quest**, through its whole life:

| Stage | What it is today | Reads as |
|---|---|---|
| offered | an owner proposal or lead: a case, an issue, a finding, a ticket, a failing check, an alert, a flaky check, a review, a crack, a weed | "a quest on offer" |
| underway | a job running | "a quest underway" |
| done | a finished job waiting for its review | "a quest to review" |

The achievements journal, which also said "quests", becomes **Deeds**. The API (`/proposals`, `/leads`, `/jobs`, `/world/quests`), the tables and the MCP tools keep their names: the word changes where people read it, nowhere a program does.

| Word | Meaning |
|---|---|
| decision | One choice waiting on a person, about one quest: what it is, the facts that decide it, and the actions open |
| hand-off | A decision sent by one person to another, who then decides it |
| ask | One message to one person about one decision, with its state: queued, sent, reminded, answered, expired |
| code | A short id for an ask, shown in the message, never reused |

## The decision

Built by the server, from data embry0 already has, so the dashboard and a chat render the same thing:

```json
{
  "id": "offer:crack:1a2b3c4d5e6f7a8b",
  "stage": "offered",
  "verdict": {"label": "High", "tone": "bad"},
  "title": "A committed service key bypasses row-level security",
  "repo": "acme/web", "area": "Auth", "at": "2026-10-27T09:12:00Z",
  "facts": [{"label": "Evidence", "value": "lib/probe.mjs:4 (+1)"}, {"label": "Size", "value": "small"}],
  "why": "Found by the scout: anyone with the repository can read every row.",
  "yes_starts": "a job that removes it and opens a pull request",
  "actions": ["start", "decline", "later", "send_to"],
  "owners": ["octocat"], "assigned_to": null,
  "links": {"proposal": "crack:1a2b3c4d5e6f7a8b"}
}
```

- **id**: `offer:<proposal id>` for a quest on offer, `done:<job id>` for a quest to review.
- **verdict**: one word or two, read before anything else.
  - On offer: the severity (critical, high, medium, low), else the kind's own word ("Production error", "Open issue").
  - Done: ready to merge (checks pass, the recommendation is merge, every done-when item met), needs a look (anything else that produced a pull request or a report), failed (no deliverable), waiting (paused on an account or a question).
- **facts**: at most six, in this order where present. Values are short; a fact with nothing to say is left out.
  - On offer: evidence, size, how often and since when, owners, cost if known.
  - Done: pull request (`#n +a −d · f files`), checks, done-when (`met/all`), sensitive paths, cost and time, slop verdict.
- **why**: one sentence. The brief's problem or why, else the first sentence of the summary.
- **yes_starts**: what accepting starts, in words (on offer only).
- **actions**:
  - On offer: `start`, `decline`, `later`, `send_to`.
  - Done: from what the quest allows today: `merge`, `send_back`, `reject`, `good`, `bad`, `run_again`, `dismiss`, plus `send_to`.

`GET /api/v1/decisions?stage=offered|done|all&for=me&repo=&limit=` lists them, worst first then newest. `for=me` keeps those handed to the caller or owned by them. `GET /api/v1/decisions/{id}` reads one. The existing action endpoints stay; the decision lists which ones apply.

## Hand-offs

**Send to…** on any decision: pick a teammate (anyone who can sign in) and add a note. The decision is now theirs: it shows in their **Waiting for me**, a chat assistant asks them (below), and whatever they do (start, decline, merge, reject, send back) is the decision. The sender is told the outcome. A hand-off can be passed on again or sent back.

- **People.** `users.github_login` links a person who signs in to the GitHub login owners are named by, so embry0 knows which owner is looking. How a chat reaches a person is the deployment's, keyed by email.
- **Who decided.** Every answer records the actor the server resolved (session, then key), never a name the client sends. A key that acts for people (`propose`) passes the person, and the server checks that person may decide: an owner of the repository or area, the hand-off's recipient, or an admin.
- **Rights.** The recipient of an open hand-off may take every action the decision lists, including merge, for that one decision only.
- `handoffs` table:
  - columns: `id`, `decision_id`, `from_actor`, `to_email`, `note`, `state` (open, decided, returned, passed, cancelled), `created_at`, `closed_at`, `outcome`;
  - `POST /api/v1/decisions/{id}/send-to` (`{"to": "<email>", "note": "..."}`), `POST /api/v1/handoffs/{id}/return`, `GET /api/v1/handoffs?for=me`;
  - a decision answered by anyone closes its open hand-off as `decided`, with the outcome.
- Webhook events `handoff.created` and `handoff.closed`.

## Asks

embry0 keeps one row per message it wants a person to get: `asks` (`id`, `decision_id`, `recipient_email`, `code`, `state`, `due_at`, `sent_at`, `message_ref`, `reminders`, `answered_at`, `answer`).

- **Who is asked.** Each owner of a decision that is not held (the same rule as today). The recipient of a hand-off is asked too, and is the only one asked while it is open.
- **When.** Within the recipient's working hours (`ASK_HOURS`, `ASK_TIMEZONE`). A reminder goes after `ASK_REMIND_HOURS` unanswered, once. The ask expires after `ASK_EXPIRE_DAYS`, and expiring answers nothing: the decision stays open on the page.
- **Since the settings document** (spec 2026-10-28), these and the other `ASK*` variables are keys of its `asks` section (`asks.hours`, `asks.timezone`, `asks.remind_hours`, `asks.expire_days`, and so on), imported from the variables once and read every tick.
- **Codes.** Four letters from the decision id, lengthened on a collision and never given to another decision, so a late answer can never land on the wrong quest.
- **Feed.** `GET /api/v1/asks/due` (the asks to send now, each with its decision rendered), `POST /api/v1/asks/{id}/sent` (`{"message_ref": "..."}`), and `POST /api/v1/asks/{code}/{action}` (`{"person": "<email>", "reason": "..."}`), which answers the decision through the same paths the dashboard uses. A `propose` key does all three. Answering is idempotent: a second click returns the first answer.
- **Events.** `ask.answered`, so the assistant updates the message it sent, and the outcome of the job a yes started goes back to that ask.

**The deployment's part** (not in embry0):
- a sender with no model in it: polls the feed, renders each decision as a card with its actions as buttons, sends it, reports `sent`;
- a button handler that checks the clicker and calls the answer endpoint with them as `person`.

A chat model may still answer questions about a quest; it never turns a reply into an answer.

## Dashboard

One `DecisionCard`: the verdict and where it is on the first line, the title, a row of facts, the why, the actions, and the details (report, evidence, diff) folded underneath. It is used by:
- the world's inbox and review scroll;
- the quest board and the start dialog;
- the owner proposals page, renamed **Quests on offer**, with every kind in its filter;
- **Waiting for me**, the decisions handed to the viewer.

## Rollout

1. The decision model and `GET /decisions` (this spec).
2. Decision cards in the dashboard, and the words.
3. Hand-offs, `users.github_login`, server-resolved deciders.
4. Asks, and the deployment's card sender and button handler.

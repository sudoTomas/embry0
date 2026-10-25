# Owner proposals: work embry0 asks a repository's owners to start, from a chat

**Goal.** embry0 finds work nobody has started: a problem the watcher saw in the logs, an issue that is open and waiting, a line of documentation the context auditor thinks is wrong. Today each waits on a dashboard someone has to open. This spec gives every such item to the repository's **owners** as a **proposal** they can answer from a chat: yes (start it), no (and why), or later. A chat assistant does the asking: it reads new proposals through embry0's MCP server, messages the owner with the proposal's id, and turns the reply into an answer through the same server.

embry0 does not message anyone itself. The assistant, the chat it uses, and who it may message are the deployment's; embry0 offers the feed, the owners, and the three answers.

The dashboard's Proposals section already lists what awaits a decision: watcher drafts and organization needs (`GET /cases?awaiting_decision=true`). Owner proposals widen that to open issues and audit findings and address each to its repository's owners; organization needs stay in that section, since they belong to an organization rather than a repository.

**Non-goals.** No chat client in embry0, no new push channel, no change to how an accepted case, issue or audit fix then runs. Merge approvals stay where they are.

## Words

| Word | Meaning |
|---|---|
| owner | A GitHub login that answers for a repository: set in the repository's preferences, else read from its `CODEOWNERS` |
| proposal | One thing an owner could start, with a stable id: `case:<id>`, `issue:<id>` or `finding:<hash>` |
| answer | `accept` (start it), `decline` (with a reason) or `snooze` (until a time), recorded with who gave it |
| assistant | The caller that asks owners in a chat: any MCP client holding a `propose` key |

## Owners

`repo_preferences.owners`: a JSON list of GitHub logins, edited like every other preference (history and undo come with it, ADR 0019). A repository without the setting falls back to the users its `CODEOWNERS` names for `*` (`@org/team` entries are skipped in this version), read from the default branch and cached for an hour. A repository with neither has no owners and its proposals go to nobody until someone sets them.

## The three kinds

| Kind | Comes from | Says | Accept does |
|---|---|---|---|
| `case` | a watcher case at stage `tracked` on an enabled stack | what keeps failing, how often, since when, and the draft ticket | the existing accept: an issue and its pipeline (`POST /cases/{id}/accept`) |
| `issue` | an issue in status `open` with no job | its title, where it came from, how long it has waited | the existing "send to triage" (`POST /issues/{id}/triage`) |
| `finding` | a row of a completed context audit's findings table | "we found this in this file; is it what you want?": the location, the claim, the evidence and the proposed fix | a context-audit fix job narrowed to that file, which opens a pull request for it even where the organization's audit pull requests are off: the owner asked for this one |

**Findings, one at a time.** An audit report's `## Findings` table has a fixed shape (Severity, Location, Claim, Evidence, Fix; the context auditor's prompt sets it). A parser reads each row; a finding's id is a hash of the repository, the file and the claim, so the next audit finding the same thing does not ask twice. Declining a finding records it as intended; until the auditor learns to skip intended claims (a follow-up), declined findings simply stay out of the feed.

## Answers

`proposal_decisions(id, proposal_id, repo, kind, decision, by, reason, until, created_at)`. An accepted, declined, or snoozed-until-later proposal leaves the feed; a snooze returns it when it ends. A case or issue that moves on by other means (accepted on the dashboard, closed, triaged) leaves the feed by itself. Every answer is also an audit-log entry naming who gave it.

## API and MCP

- `GET /proposals?owner=<login>&kind=&since=` (read): the open proposals, newest first, each `{ id, kind, repo, owners, title, summary, details, action, url, created_at }`. `summary` is a sentence for a chat message; `details` the longer evidence.
- `GET /proposals/{id}`, `POST /proposals/{id}/accept`, `/decline` (`reason`), `/snooze` (`until`), each with `by`, the GitHub login of the person who answered.
- MCP tools `proposal_list`, `proposal_get`, `proposal_accept`, `proposal_decline`, `proposal_snooze`; the three answers require `confirm`, as other MCP writes do.
- **A `propose` key scope**, like the `merge` scope: read, plus answering proposals and the actions behind them, and nothing else; an admin mints it, and its repo allowlist limits which repositories it can answer for. An assistant gets its own key, so its reach is exactly that.

## The assistant (outside embry0)

The deployment's assistant polls `proposal_list` for the owners it serves (every quarter hour, say), messages each owner what is new with the proposal id in the message, and on a reply reads the proposal by id and answers it. Because a chat's history may not survive between the message and the reply, every message stands alone and every answer names its id. Who may answer is the assistant's to check: it must confirm the person replying is an owner it serves before calling an answer tool, and a deployment can start with one person.

## Slices

1. **Owners and the feed:** the `owners` preference and the CODEOWNERS fallback, the findings parser, `GET /proposals`, the answers and their table, the `propose` scope, the MCP tools. A Proposals view of the feed per owner on the dashboard.
2. **The assistant**, in the deployment: its key, its schedule, its message, and its check on who may answer.
3. **Intended claims back to the auditor:** declined findings passed to the next audit, so it stops raising them.

## Testing

Owners from preferences and from `CODEOWNERS`, teams skipped; each kind's source query against Postgres with seeded rows; the findings parser on real-shaped tables, malformed rows skipped, the id stable across audits; each answer for each kind, and a second answer refused; snooze returning a proposal; a proposal leaving the feed when its case or issue moves on; the `propose` scope allowed exactly its routes; the MCP tools' confirm gates.

## Addendum (2026-10-25): briefs and the readiness floor

An owner could not say yes or no from a proposal's `summary`, and its id says nothing to a person. Each proposal now carries a `brief`, its title, problem, fix and size in plain words, written by a model in the background, and a backlog ticket is asked only at readiness 60 and up. See [proposal briefs](2026-10-25-proposal-briefs-design.md).

# The guild: embry0's home screen

embry0 opens on its guild: the agents at work, drawn as characters in a hall and on a map. The page around the pictures is embry0's usual interface and speaks its usual words (agents, jobs, review); the pictures and the activity box speak the game's (loot, XP, attack, defeated). Both call a piece of work a quest through its whole life (spec 2026-10-27, decisions): a quest on offer (what embry0 found), a quest underway (a job running), a quest to review (a job done). The achievements journal is Deeds. The design is in [the spec](superpowers/specs/2026-10-25-world-design.md). It is on by default, and an operator can [turn it off](#turning-the-game-off).

## The screen

- **Finished, waiting for review** (top): finished jobs nobody reviewed yet, failures and open pull requests first. A job stays here until you rate it, merge it, send it back or reject it. It covers the jobs a person started and any that opened a pull request; the watcher's routine log reads are not here.
- **Agents** (the Hall tab): the hall's inside as a picture, every agent at its station (empty while it is busy, a `?` while it waits for you), the quest board and the loot chest, which opens once a pull request came back and turns gold once one merged; a click on any of them does what the card, the board or the inbox does. Below it every agent's card: its level, the gear it earned, its best skills, what it is doing now and its record (jobs, how many succeeded, how you rated it). A job with no activity for 20 minutes is flagged, and an agent idle for two weeks too. Click an agent for its panel.
- **Quests on offer**: what embry0 found, of every kind `GET /api/v1/lead-kinds` names, each with a level from its severity and reach, coloured against your strongest agent's level. Each row wears its creature and the game's verb for it. Filter by repository, area, kind and level. A kind's names come from the backend's one lead-kind catalogue (`GET /api/v1/lead-kinds`, read once a visit and kept); its badge's hover says what it is and what found it. Click one to start it.
- **Map** (the Map tab): the repositories around the guild hall, their areas as towns, the quests on offer standing in them as creatures, and each job's agent walking to its quest.
- **Activity** (bottom left): what changed since you opened the page, in the game's words: a party setting out, needing your answer, coming back, falling. A failure names its cause, the provider's own words when the job kept them ("weekly limit · resets ..."). Each line has a copy button that copies a debug block (the time in UTC, the line, the job's title, the agent, the repository and area, the job id, its error code and exact message for a failure, and its page, `/jobs/<id>`); Copy all takes every line shown. The box folds to a tab and closes; Activity in the page header opens it again, and the browser remembers the choice. On a phone it starts folded.

Every panel and dialog (a job's report, an agent's panel, a lead, New task) closes with its Close button, Escape or a click outside it, and gives focus back to what opened it.

## Starting work

From Quests on offer: the quest's dialog says what it is, where, the evidence and what starting it runs; pick the model and key with Run with, and press Start. That is the owner proposal's yes, recorded as you.

**New task** gives one agent a task you write, on a repository you pick. The developer runs the usual pipeline; a specialist (Why, How, Kintsugi and the rest) runs its own. Triage, review and QA work only inside another job.

## Watching

An agent's panel shows what it is doing now. For a running job: the step it is on, its last tool calls and words, live; its question, if it asked one, with a box to answer; and Cancel the job. Job history lists every job it ran, with the outcome, the pull request, its share of the cost and time and your rating; a row opens that job's report.

## Reviewing the work

A job's report says what it did, the pull request (files and lines changed, the checks' verdict), the XP each agent earned, the cost and the time; a failed one says why, with its error code. Then:

- **Merge**: through the job's merge approval when it has one ([merge approvals](merge-approvals.md)); otherwise "I merged it" records that you did.
- **Send back with notes**: the same work, picked up on its own pull request branch with your notes as the task, the original session resumed. Takes Run with.
- **Reject**: the work is not wanted; tick "Close the pull request" to close it with your note as its last comment.
- **Rate it**: good, needs work or bad, with a note. Ratings show in the agent's record and job history; they do not change XP.

Reviewing is not part of the game: these buttons, like the Decisions page's, call the job's own review routes ([API](api.md#reviewing-a-job)).

Only an admin session reviews, starts and cancels; others see everything read-only.

## Turning the game off

The guild is the home screen of the game layer, which every deployment gets until it says otherwise. To run a plain operations dashboard instead, write the `ui` section of the [settings document](configuration.md#settings-document) (Settings → Policy, `embry0 settings apply`, or the `settings_set` MCP tool):

```yaml
ui:
  game: false     # or `off`
```

With the game off:

- `/` opens the Overview, with the brief above it, and the sidebar's Overview group holds the Overview alone.
- The guild, the Achievements page, the chat box, each agent's level, skill sheet and outfit (the Agents pages, a job's party, the pipeline graph), and the Settings page's Game content tab are not shown. A link to `/guild`, `/achievements` or `/overview` opens the Overview at `/`.
- The dashboard reads no progression, and the game's routes (`GET /world`, a character, a quest's scroll, `GET /progression`, `POST /progression/rebuild`) answer 404 saying the game is off ([API](api.md#the-world)).
- Everything else works as before. Reviewing a finished job is not part of the game ([Reviewing the work](#reviewing-the-work)), so the Decisions page, a job's page and the review routes work as they did. The Decisions page drops the game's word: its tabs read Jobs to review and Work on offer, and a finished job's card is labelled Job done instead of Quest done (in `GET /decisions` too). Each kind of work on offer keeps its plain name (Production error, Crack, Failing check), from `GET /api/v1/lead-kinds`, which is not a route of the game; creatures, leaders, verbs and levels are the game's and are not shown. The content pack can still be read and changed through the API, the CLI and MCP, ready for when the game is turned on again, and the experience keeps counting from the recorded work, so nothing is lost while it is off.

`home: overview` keeps the game on but opens the dashboard on the Overview; the guild is then at `/guild`, and a link into the guild written while it was at `/` (`/?quest=…`, `?character=`, `?lead=`, `?write=`, `?view=`) opens it there, with its query. While `game` is false, `home` is not read.

A change shows on every dashboard within a minute, and at once on the one that saved it. Nothing restarts. A browser keeps the last answer and draws with it at once on its next visit, then reads the section again; so a browser that last saw the game on shows it for the moment that read takes after the game is turned off. A browser's first visit waits for the read, with the header alone, so it never shows the game while it is off; the wait is at most 3 seconds, after which it draws with the defaults. The setting is the deployment's: an organization cannot turn the game off for its own people, because the dashboard has no viewer's organization to read it for: a session carries an email, a name and roles, and an API key at most a repository allowlist, and neither names an organization. The game itself is one account whose characters are the deployment's agents.

## Art

The hall's inside and the map are pixel pictures, each in a dark frame with a gold inlay. `frontend/public/world/manifest.json` names the art and `frontend/public/world/README.md` is its schema. Anything the manifest does not name is drawn as a placeholder. Experience, level-ups and hits show as effects over the characters; reduced motion stops them.

The world is at night (Nightfall, [art direction](superpowers/specs/2026-10-27-art-direction-design.md)): every sheet and character in a scene is re-lit toward the page's black, and gold light falls where the work is. In the hall it falls at the torches and the quest board, and at the loot chest while it holds loot. On the map it falls at the hall's door, and every party out on a quest carries one. Effects stay as drawn.

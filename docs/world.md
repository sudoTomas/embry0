# The guild: embry0's home screen

embry0 opens on its guild: the agents at work, drawn as characters in a hall and on a map. The page around the pictures is embry0's usual interface and speaks its usual words (agents, jobs, review); the pictures and the activity box speak the game's (loot, XP, attack, defeated). Both call a piece of work a quest through its whole life (spec 2026-10-27, decisions): a quest on offer (what embry0 found), a quest underway (a job running), a quest to review (a job done). The achievements journal is Deeds. The design is in [the spec](superpowers/specs/2026-10-25-world-design.md).

## The screen

- **Finished, waiting for review** (top): finished jobs nobody reviewed yet, failures and open pull requests first. A job stays here until you rate it, merge it, send it back or reject it. It covers the jobs a person started and any that opened a pull request; the watcher's routine log reads are not here.
- **Agents** (the Hall tab): the hall's inside as a picture, every agent at its station (empty while it is busy, a `?` while it waits for you), the quest board and the loot chest, which opens once a pull request came back and turns gold once one merged; a click on any of them does what the card, the board or the inbox does. Below it every agent's card: its level, the gear it earned, its best skills, what it is doing now and its record (jobs, how many succeeded, how you rated it). A job with no activity for 20 minutes is flagged, and an agent idle for two weeks too. Click an agent for its panel.
- **Quests on offer**: what embry0 found (watcher problems, open issues, outdated instructions, broken builds, alerts, flaky tests, unanswered reviews, backlog tickets), each with a level from its severity and reach, coloured against your strongest agent's level. Each row wears its creature and the game's verb for it. Filter by repository, area, kind and level. Click one to start it.
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

Only an admin session reviews, starts and cancels; others see everything read-only.

## Art

The hall's inside and the map are pixel pictures, each in a plain card. `frontend/public/world/manifest.json` names the art and `frontend/public/world/README.md` is its schema. Anything the manifest does not name is drawn as a placeholder. Experience, level-ups and hits show as effects over the characters; reduced motion stops them.

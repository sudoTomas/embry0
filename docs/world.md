# The guild: embry0's home screen

embry0 opens on your guild: the agents are your characters, the work embry0 found is the quest board, and every job is a quest a party goes out on and comes back from. The design is in [the spec](superpowers/specs/2026-10-25-world-design.md).

## The screen

- **Quests back** (top): the quests that came back and wait for your judgement, failures and open pull requests first. A quest stays here until you rate it, merge it, send it back or reject it. It covers the quests a person sent and any that brought back a pull request; the watcher's routine log reads are not here.
- **Guild hall**: every character with its level, the gear it earned, its best skills, what it is doing now, and its record (quests, how many it won, how you rated it). A party that has gone quiet for 20 minutes is flagged, and a character idle for two weeks too. Click a character for its panel.
- **Quest board**: what embry0 found (watcher problems, open issues, outdated instructions, and later backlog tickets and the finders' leads), each with a level from its severity and reach, coloured against your strongest character's level. Filter by region, area, kind and level. Click one to send a party.
- **Map**: the regions (repositories) around the guild hall, their towns (areas), the quests standing in them as creatures, and each party walking to its quest.
- **The chat box** (bottom left): what changed since you opened the page: a party setting out, needing your answer, coming back, falling.

## Sending a party

From the board: the quest's panel says what it is, where, the evidence and what sending someone starts; pick the model and key with Run with, and press its verb (Attack, Start quest, Repair, Talk). That is the owner proposal's yes, recorded as you.

**Write a quest** gives one character a task you write, on a repository you pick. The developer runs the usual pipeline; a specialist (Why, How, Kintsugi and the rest) runs its own. Triage, review and QA work only inside another quest.

## Watching

A character's panel shows what it is doing now. For one that is out: the step it is on, its last tool calls and words, live; its question, if it asked one, with a box to answer; and Recall, which cancels its job. The quest log lists every quest it went on, with the outcome, the pull request, its share of the cost and time and your rating; a row opens that quest's scroll.

## Judging the work

A quest's scroll says what it did (its report), the loot (the pull request, files and lines changed, the checks' verdict), the experience each character earned, the cost and the time; a failed one says why. Then:

- **Merge**: through the quest's merge approval when it has one ([merge approvals](merge-approvals.md)); otherwise "I merged it" records that you did.
- **Send back with notes**: the same work, picked up on its own pull request branch with your notes as the task, the original session resumed. Takes Run with.
- **Reject**: the work is not wanted; tick "Close the pull request" to close it with your note as its last comment.
- **Rate it**: good, needs work or bad, with a note. Ratings show in the character's record and quest log; they do not change experience.

Only an admin session judges, sends and recalls; others see everything read-only.

## Art

The hall's inside sits above the cards: every character at its station (empty while it is out, a `?` while it waits for you), the quest board, and the loot chest, which opens once a pull request came back and turns gold once one merged; a click on any of them does what the card, the board or the inbox does. The map is a tile map: the guild hall in the middle, a road to each region's square, the quests as creatures standing in their region (a click sends a party), each party walking out to its quest and back. Experience, level-ups and hits show as effects over the characters; reduced motion stops them.

`frontend/public/world/manifest.json` names the art and `frontend/public/world/README.md` is its schema. Anything the manifest does not name is drawn as a placeholder.

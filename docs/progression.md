# Progression

Every agent type, built-in or custom, is a **character**. The work embry0 already records, the tool calls of each run and the outcomes the database keeps (a merged pull request, a QA run passed, an issue finished, a job that came back from a failure), becomes experience in the character's skills. Levels, achievements, quests and diaries are recomputed from those rows, so they are always what the history says; only the experience of days that can no longer change is kept, folded once, so a read does not go through all of history again (see [How experience is read](#how-experience-is-read)). The guild screen ([world.md](world.md)), the agent pages and the Achievements page show them; `GET /api/v1/progression` returns all of it ([api.md](api.md)).

Everything below (the skills, the curve and rates, the tiers, the rares, the achievements, quests and diaries, and the chat box's words) is the **content pack**'s, not code: see [The content pack](#the-content-pack). This page describes the pack embry0 ships.

## Skills and levels

A character has 23 skills in four groups:

| Group | Skills |
|---|---|
| Build | Coding, Version control, Building, Shell, Databases |
| Verify | Testing, Quality assurance, Browsing, Reviewing, Linting, Security |
| Investigate | Reading, Searching, Debugging, Research, Triage |
| Care | Documentation, Design, Operations, Planning, Delegation, Communication, Recovery |

A run's tool calls train the skill each call's category belongs to (an edit to a test file is `edit:tests`, which trains Testing; a log query is `log:log-query`, which trains Debugging), and some outcomes train one skill on their own (a merge trains Coding, a QA pass Quality assurance, a comeback Recovery). Each skill levels from 1 to 99. A character's **level**, 1 to 126, comes from its five best skills, so depth and breadth both count; its **total level** is its 23 levels added up.

## Tiers and what a character wears

A character's best skill sets the tier it wears, a medal ladder:

| Tier | From level |
|---|---|
| Bronze | 1 |
| Silver | 15 |
| Gold | 30 |
| Platinum | 45 |
| Diamond | 60 |
| Obsidian | 75 |
| Divine | 90 |

Its avatar picks a style: melee climbs plate (helm, chestplate, greaves, heater shield, sabre), ranged climbs leather to scale to pale hide with a bow, magic climbs the robe orders with a staff. From Platinum up it carries the party cape and an amulet. A skill at 99 replaces the party cape with that skill's **mastery cloak**, trimmed for two or more. A **rare** replaces the head item: a merge on 25 December earns a paper crown, on 31 October a harvest mask, and the first run of a new year a golden paper crown.

## Achievements, quests and diaries

- **Achievements:** fifty, in eight categories (Shipping, Hunting, Questing, Lore, Guarding, Grit, Growth, Oddities). Most are tiered from Bronze to Divine by how many times the outcome happened; some are objectives, done once; a few are secret until reached.
- **Quests:** four short goals the account completes once, for quest points and an experience reward to the characters that did the work: First Steps, Try, Try Again, Gatekeeper and Divine Warden. Every quest done gives the character with the highest total level the quest cloak; Divine Warden trims every cloak in the Divine metal.
- **Diaries:** one per repository, with tasks in four tiers, Easy to Elite (for example: triage an issue; merge ten pull requests; go 30 days without a failed job).

## The content pack

Every name, number and word above comes from one document, the content pack. Its schema is code (`embry0/services/progression_pack.py`), and so is what it may name (`embry0/services/progression_vocabulary.py`): the metrics an achievement can count and the parameters each takes, the quest and diary task kinds, the action categories, the outcome sources and the art the dashboard draws. Its values are data. embry0 ships one, `embry0/services/progression_pack_default.yaml`, commented section by section; the design is [the spec](superpowers/specs/2026-10-28-progression-pack-design.md).

- **One pack per deployment.** Characters are agent types, which are deployment-wide, and the account is the deployment.
- **A pack re-prices the whole history.** A trace stores what each tool call was, an *action category* such as `shell:test-runner` or `edit:docs`, never a skill; the pack maps each category to the skill it trains and prices it by its kind. Runs recorded before the categories (since migration 107) are translated when read; older runs keep only their tool names, so a shell call is plain shell and an edit is source.
- **Achievements are a metric and its parameters.** The facts query runs one SQL fragment per metric the pack uses, with the pack's parameters and timezone bound as values; a pack never carries SQL.
- **The calendar is the pack's `timezone`** (UTC in the shipped pack): the day a rare drops on, the hour a merge counts as late, the day a hot streak counts in. The texts leave it out; the Achievements page names it once, beside the note on secret achievements, where the oddities are.
- **The dashboard keeps no copy.** `GET /api/v1/progression` carries the pack's tiers, rares, skill groups and cloak colours, diary tiers, messages and nouns, and the pages read them from there. `docs/contracts/progression.json` pins the art a pack may name on both sides.

- **Texts take one small grammar.** `{name}` is a value, `{name:,}` a number with thousands separators, and `{{` and `}}` are literal braces. Anything else in braces is refused, so a text can never break or slow a read. Each text may name only its own values: an achievement's or a quest's parameters, a diary task's, or a message's (`{character}`, `{skill}`, `{level}`...).

Check a pack file with `embry0 progression-pack check <file>` (or `python -m embry0.services.progression_pack --check <file>`): it prints every problem with its key, then any warning, such as a category no skill is trained by.

## Changing the content

An admin changes the pack on the dashboard (**Settings → Game content**), with `embry0 progression-pack`, through the MCP tools or through the API ([api.md](api.md#progression-content-pack)). Anyone can read it; the tab is read-only for everyone else. A deployment that never changes it follows the shipped default, so a release that changes the default reaches it as code would.

- **A whole document.** A customised pack is the full pack, stored as written with its comments, beside its parsed values. Start from the shipped default (`embry0 progression-pack export --default`, or the editor as it opens) and change what you want: rename or retune skills, tiers, rares, achievements, quests and diaries, translate the messages and nouns, set the `timezone`.
- **Draft, preview, activate.** *Save draft* stores one draft; nothing anyone sees changes. *Preview* computes the progression under the draft from the whole history, now, and shows each character's level, total level and experience and the account's achievements unlocked, before and after; both sides are read from the raw rows, never from the [rollup](#how-experience-is-read), so the difference is the draft's alone. *Activate* makes the draft the pack in effect; every page re-prices the history at its next read. One preview runs at a time, since it reads the whole history.
- **One step, for files and tools.** `embry0 progression-pack apply <file>` (and `PUT /api/v1/progression/pack`, and the MCP tool `progression_pack_set`) checks and activates in one call, showing the diff first; `diff` and `?dry_run=1` show the problems, the warnings and the diff without storing anything, and a file equal to the pack in effect records nothing. `check <file>` validates offline with the same checks.
- **History and undo.** Every activation is a version, recording who, why and how (`person` for the dashboard and API, `file` for `apply`, `restore`, `reset`, `upgrade`); the newest 100 earlier versions are kept. *Restore* puts an earlier version back as a new version, so it is undone the same way.
- **Reset to default.** A new version that follows the shipped default: the pack in effect becomes whatever the running release ships.
- **When the shipped default moves on.** A customised pack records the default it was taken from. When a release ships another, the tab says so and shows the diff between the two defaults, so you can carry over what you want. Saving with *I have taken in the new default* (`?rebase=1`) records that the pack is now based on this release's default, and the notice goes.
- **Two people at once.** Every write names the version it replaces (`If-Match`, the `ETag` a read returned): a write over someone else's newer change is refused (409) instead of overwriting it, and *Activate* names the draft its preview showed. A draft also records the version in effect when it was saved: if someone activates another pack in the meantime, the draft cannot be activated over it unseen. The tab says so and previews the draft again against the new pack; save the draft again, carrying over what you want to keep, then activate it.
- **Who changed it.** The history names who made each version as the audit log does: an admin sees everyone, anyone else only their own changes.

**What a write refuses.** A 422 lists every problem by its key, such as `achievements[3].thresholds`, never echoing a value: an unknown key or a wrong type, a reference to a skill, tier, category, metric, kind or art that does not exist, thresholds that do not match the tiers, a placeholder a text may not use, a timezone the database does not know (Python and Postgres read different tz databases; the name must be known to both. Should a stored pack's zone still be unknown to the database, the achievements that count a day or an hour are counted on UTC, and both the Game content tab and the Achievements page say so), a key written twice, YAML aliases and merge keys, and a document over 256 KiB (413). Warnings never block: an action category no skill is trained by, or an agent named in a rate that is not defined (yet).

### From the command line and MCP

`embry0 progression-pack` works on a pack file:

| Command | What it does |
|---|---|
| `check <file>` | Validates offline, with the API's own checks, and prints every problem as `key: msg`. Only the database's timezone check waits for a write |
| `diff <file>` | A dry run through the API: whether it would change, the sections, the warnings and the text diff. Stores nothing |
| `apply <file> [--reason TEXT] [--yes]` | Shows the diff, asks, then activates it with source `file`. A file equal to the pack in effect records nothing; without a terminal it needs `--yes`; a pack changed since the diff is refused (409) |
| `export [--default] [-o FILE]` | Writes the pack in effect, or the shipped default, as YAML. When the stored version is refused, that is the shipped default, and it says so |

`check` exits 1 on a problem. `diff` and `apply` exit 1 on a refusal (422 or 409), and 2 when the orchestrator cannot be reached or refuses the key. Like `embry0 onboard`, they read `EMBRY0_API_URL` and the API key (`--api-url`, `--api-key`).

| MCP tool | Effect |
|---|---|
| `progression_pack_get()` | The pack in effect, with its text |
| `progression_pack_set(text, expected_version=None, dry_run=False, confirm=False)` | Checks and activates the whole pack. Pass the `version` `progression_pack_get` returned as `expected_version`, so a pack someone activated in between is refused (409) rather than overwritten. A dry run needs no `confirm` and stores nothing; a write without `confirm=true` returns `confirmation_required` and changes nothing. Needs an admin key |
| `progression_pack_reset(confirm=False)` | Follows the shipped default; without `confirm=true` it returns `confirmation_required` |

**A stored pack the release refuses.** A pack stored under an older schema is upgraded when the orchestrator starts, as a new version with source `upgrade`; its text is written again from its values, so its comments are lost, and the version's reason says so. A stored pack the running release still refuses (one it cannot upgrade, or one a newer release wrote before a rollback) is never served: the shipped default is, the orchestrator logs `progression_pack_stored_invalid` once, and the tab lists the problems. Save a corrected pack, restore another version, or reset.

## How experience is read

Experience is one pass over every event in time order: each event's XP goes to its character's skill, and each level crossed, character level reached and rare earned is recorded at the event's time. Rather than make that pass over all of history on every read, embry0 keeps a **rollup** of the days that can no longer change, per content pack (migration 131, `embry0/services/progression_rollup.py`):

- **A day is closed** once it ended 48 hours ago on the pack's calendar. A run keeps the time it started and can finish much later, and a merge arrives with the forge's time, so a day settles for two days before it is folded.
- **What is stored** for the closed days: the XP per day, character and skill; every level-up, character level-up and rare drop at its exact time; and what the pass carries past the last closed day (the character levels reached and the year of the last run, which the new-year rare reads).
- **A read** takes the rollup and passes over the raw events after its **boundary**, the instant the newest closed day ended: about three days of rows. Its answer equals a pass over everything, field for field, level-up times included. Achievements, quests and diaries still read the raw rows on every read; the journal they read holds only the runs of the agents the pack's quests and diary tasks name.
- **The timezone is applied in Python.** The rollup's days and boundary come from the pack's zone in Python's timezone database, as in a pass over everything, and never from Postgres. When Postgres refuses the pack's timezone and the achievements' facts fall back to UTC, the experience, its days and the rares stay on the pack's calendar.
- **Writes happen in the background,** never inside a read, and one at a time: within an orchestrator every write, an admin's rebuild included, waits its turn, and across orchestrators a writer holds a Postgres advisory lock (`progression_rollup`), so two of them build once. A write is one transaction, so a reader sees the old rollup or the new one, never half of one. While no rollup exists for the active pack (the first read, or a pack change), reads pass over every raw row as before, and the rollup is built. A write that fails is retried after a minute, then after twice as long each time up to an hour, not on every read.

| Write | When |
|---|---|
| Build | No rollup for the active pack's hash: the first read after an upgrade, or after the pack changed (an activation, a restore or a reset). Also when the rollup was folded by a release whose folding code differs (`FOLD_VERSION`, a digest of the modules that decide what an event earns), since the pack's hash covers only what an operator can change. Every other rollup is deleted. |
| Advance | A day closed since the last write. Only the newly closed days are folded and appended. |
| Rebuild | Once a night, at the first read after 03:00 on the pack's calendar; when a quest's XP reward moved into the closed days (a late merge completes a quest in the past; reads pass over every raw row until it is done); and on `POST /api/v1/progression/rebuild`. |

**Staleness.** An event recorded more than 48 hours after its own time, for a day the rollup already holds, is not in the experience until the next rebuild, so at most a day late. The same goes for a row of a closed day that changes after it was folded, such as a job's last run, which names the character a resumed job credits. An event's time never moves: a job's events happen when it finished (a merge when the forge says it merged), never at the time its row was last touched, so a job that opened a pull request earns that XP when it finishes. Nor does an outcome come and go with the job's pull request: a job that finished its work (`completed`, and the `pr_merged` and `pr_closed` that merge sync moves it to) keeps its experience, its achievement progress and its diary tasks when the pull request merges, closes or reopens, so a context audit or a QA run counts the same before and after. `GET /api/v1/progression` says how old the rollup is in `rollup`: `through_day`, the newest closed day; `boundary`, from which every event counts at once; `built_at`, the last write; and `rebuilt_at`, the last full rebuild. `rollup` is `null` when the answer passed over every raw row. After a backfill of old merges, an admin can rebuild at once with `POST /api/v1/progression/rebuild` ([api.md](api.md#progression)).

## Turning it off

There is no setting yet; #177 tracks one per deployment and organization.

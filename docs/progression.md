# Progression

Every agent type, built-in or custom, is a **character**. The work embry0 already records, the tool calls of each run and the outcomes the database keeps (a merged pull request, a QA run passed, an issue finished, a job that came back from a failure), becomes experience in the character's skills. Nothing is stored for it: levels, achievements, quests and diaries are recomputed from those rows, so they are always what the history says. The guild screen ([world.md](world.md)), the agent pages and the Achievements page show them; `GET /api/v1/progression` returns all of it ([api.md](api.md)).

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
- **The calendar is the pack's `timezone`** (UTC in the shipped pack): the day a rare drops on, the hour a merge counts as late, the day a hot streak counts in.
- **The dashboard keeps no copy.** `GET /api/v1/progression` carries the pack's tiers, rares, skill groups and cloak colours, diary tiers, messages and nouns, and the pages read them from there. `docs/contracts/progression.json` pins the art a pack may name on both sides.

Check a pack file with `python -m embry0.services.progression_pack --check <file>`: it prints every problem with its key. Today every deployment reads the shipped pack; storing and activating another (with a draft, a preview and a reset to the shipped default) is #176's next step.

## Turning it off

There is no setting yet; #177 tracks one per deployment and organization.

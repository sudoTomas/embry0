# Progression

Every agent type, built-in or custom, is a **character**. The work embry0 already records, the tool calls of each run and the outcomes the database keeps (a merged pull request, a QA run passed, an issue finished, a job that came back from a failure), becomes experience in the character's skills. Nothing is stored for it: levels, achievements, quests and diaries are recomputed from those rows, so they are always what the history says. The guild screen ([world.md](world.md)), the agent pages and the Achievements page show them; `GET /api/v1/progression` returns all of it ([api.md](api.md)).

## Skills and levels

A character has 23 skills in four groups:

| Group | Skills |
|---|---|
| Build | Coding, Version control, Building, Shell, Databases |
| Verify | Testing, Quality assurance, Browsing, Reviewing, Linting, Security |
| Investigate | Reading, Searching, Debugging, Research, Triage |
| Care | Documentation, Design, Operations, Planning, Delegation, Communication, Recovery |

A run's tool calls train the skill each call belongs to (an edit to a test file trains Testing, a log query Debugging), and some outcomes train one skill on their own (a merge trains Coding, a QA pass Quality assurance, a comeback Recovery). Each skill levels from 1 to 99. A character's **level**, 1 to 126, comes from its five best skills, so depth and breadth both count; its **total level** is its 23 levels added up.

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

## Turning it off

There is no setting yet; #177 tracks one per deployment and organization. The content is code today; #176 tracks moving it into a content pack an operator can change.

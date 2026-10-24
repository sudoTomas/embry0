# Progression and achievements: characters, an account, and what they earn

**Goal.** The work embry0 already records becomes experience, the way an old-school adventure game counts it. **Every agent is a character** with the same 23 development skills, each levelled from 1 to 99 on the classic experience curve by what that agent does in its jobs. **The embry0 instance is the player's account**: its experience is all its characters' experience, and its achievement diary, quests and repo diaries count across everything the account has done. Unlocks show on an account page, as chat-box messages, on agent cards, and on the sprites, which wear the gear, skill capes and rares their character earned.

*Revised 2026-10-24.* The first version gave the instance 21 skills, one per agent plus three of its own, which made an agent's level read as the player's. Levels now belong to characters, achievements to the account, and the skills are ones a development agent actually uses.

The tone is the old-school adventure game the roster already draws (the game UI design, 2026-10-18, and the adventurer roster, 2026-10-20). The reference for achievements is the Hermes Agent achievements plugin (MIT): stable ids, tiered progress, secret entries, a "What counts" line per entry, all recomputed from history.

**Non-goals.** Until the trust slice (below), levels are read, never gates. No leaderboard across deployments, no network calls, no sounds. No trademarked names in code, docs or strings; the art is original pixel art in the style, never copied. No new runtime dependency.

## Words

| Word | Meaning |
|---|---|
| character | An agent type, built-in or custom, with its own 23 skills. A custom agent is its own character from level 1 |
| account | The embry0 instance: the player who owns every character |
| skill | One of the 23 below. In code and URLs it is `stat` (`/progression`), because embry0's **Skills** page already means agent skill packs |
| action | One tool call of a run, classified into the skill it trains |
| experience (XP) | Points a character's skill gains from an action or an outcome the database records |
| level | 1 to 99 from XP, on the classic curve: `xp(L) = floor(sum_{n=1}^{L-1} floor(n + 300 * 2^(n/7)) / 4)`; level 99 is 13,034,431 XP and level 92 is half of that |
| total level | A character's 23 levels added up, 23 to 2,277: the number on its card |
| best skill | A character's highest level; it sets the gear tier the character wears |
| tier | Bronze, Iron, Steel, Mithril, Adamant, Rune, Divine: an achievement's seven steps, and the gear a character wears |

## Skills

Each skill echoes one classic skill, which gives it its icon and its cape colours.

| Group | Skill | Echoes | Trained by |
|---|---|---|---|
| Build | Coding | Smithing | edits to source files; a pull request merged |
| Build | Version control | Woodcutting | git commands; a pull request opened |
| Build | Building | Construction | build, compile and install commands |
| Build | Shell | Strength | every other shell command |
| Build | Databases | Mining | SQL and migration edits, database commands |
| Verify | Testing | Cooking | test-runner commands; edits to test files |
| Verify | Quality assurance | Herblore | a QA job that completed |
| Verify | Browsing | Agility | browser actions (navigate, click, screenshot, evaluate) |
| Verify | Reviewing | Defence | a review that finished |
| Verify | Linting | Crafting | lint, format and type-check commands |
| Verify | Security | Prayer | security-scan commands; a blast-radius run |
| Investigate | Reading | Fishing | file reads and globs |
| Investigate | Searching | Hunter | grep and tool searches |
| Investigate | Debugging | Slayer | log queries |
| Investigate | Research | Magic | web searches and fetches; a research run |
| Investigate | Triage | Attack | an issue triaged |
| Care | Documentation | Fletching | edits to docs; outdated claims a context audit found |
| Care | Design | Runecraft | a design audit; edits to styles and assets |
| Care | Operations | Firemaking | container, deploy and infra commands and files; an ops or onboarding run |
| Care | Planning | Farming | plan and to-do updates |
| Care | Delegation | Thieving | a sub-agent started |
| Care | Communication | Ranged | a question the agent asked a person |
| Care | Recovery | Hitpoints | a merge on an issue whose earlier job failed; an interrupted job resumed to the end |

**Actions.** The runner already counts each run's tool calls by name (`traces.tools_called`), and it sees each call's input. From this slice on it also classifies every call into its skill and stores the counts on the trace (`traces.skill_actions`, a new JSON column): a shell command by its program (`git`, a test runner, a linter, a build tool, `docker`, a scanner), a file edit by its path (docs, tests, migrations, styles, infra files, else Coding). Older traces have only tool names, so their shell calls train Shell and their edits train Coding. Version control, Building, Databases, Linting, the test-runner half of Testing and Security therefore start near level 1 and fill from now on.

**Rates.** An action earns a small fixed XP for its skill; an outcome (a merge, a QA pass, a finished review, a triaged issue) earns a large one. The starting rates come from the current history, chosen so that the busiest character's main skill reaches 99 in about a year and its first levels come in the first week:

| Source | XP |
|---|---|
| an edit (Coding, Documentation, Testing, Design, Databases, Operations by path) | 1,400 |
| a shell command (by its kind) | 600 |
| a file read or glob; a search | 1,000; 800 |
| a browser action | 1,000 |
| a log query | 400 |
| a web search or fetch; a sub-agent; a plan update | 2,000 |
| a pull request merged (Coding); opened (Version control) | 60,000; 20,000 |
| a QA job completed | 50,000 |
| a review finished | 30,000 |
| an issue triaged | 2,000 |
| a context audit completed, plus 1,000 per outdated claim (Documentation) | 40,000 |
| a research, ops, onboarding, design-audit or blast-radius run | 40,000 |
| a question asked | 20,000 |
| a recovery (comeback merge or resumed job) | 90,000 |

Failed, refused and abandoned runs earn nothing; an action counts only on a run that ended without an error. XP is recomputed from the rows, never stored, so a changed rate re-reads the past the same way for everyone.

## The account

The account shows its XP (every character's added up), the account total (every character's total level added up), its characters as a roster, and its achievement diary. Work no character does (the watcher's log reads and healed cases, the schedules' starts) earns no levels; it counts toward the account's achievements only.

## Achievements

An achievement is data: `{ id, name, what_counts, metric, thresholds[7], secret? }`. `id` is stable forever (it keys the unlock); `thresholds` are the metric's value for Bronze through Divine. An achievement is **unlocked** at its first tier, **discovered** with its progress shown while locked, or **secret**, shown as `???` until its first signal. Its unlock time is the time the metric crossed the threshold, so it is derived from history like XP. Every achievement counts across the whole account.

| Name | What counts | Tiers (Bronze → Divine) |
|---|---|---|
| Fresh off the Tutorial | jobs completed | 1, 25, 100, 250, 1,000, 2,500, 5,000 |
| Forged in Fire | pull requests merged | 1, 5, 15, 30, 60, 120, 250 |
| Guardian of the Gate | reviews finished | 1, 10, 25, 50, 100, 250, 500 |
| Potion Master | QA jobs completed | 1, 5, 15, 30, 60, 120, 250 |
| Slayer Task Complete | healing cases judged healed | 1, 3, 10, 25, 50, 100, 200 |
| Prospector | watcher runs that read logs | 10, 100, 500, 1,000, 2,500, 5,000, 10,000 |
| Green Thumb | audits the schedules started | 1, 10, 25, 50, 100, 250, 500 |
| Lines Straightened | outdated claims a context audit found | 10, 50, 100, 250, 500, 1,000, 2,500 |
| Bank Value | USD the jobs spent | 10, 50, 100, 250, 500, 1,000, 2,500 |
| Tick Perfect | jobs finished within 15 seconds | 1, 5, 10, 25, 50, 100, 250 |
| Wise Old Agent | the highest total level of any character | 100, 250, 500, 750, 1,000, 1,500, 2,000 |
| Skill Cape | skill capes earned across every character | 1, 2, 5, 10, 23, 50, 100 |
| Oh Dear, You Are Dead | merges on an issue whose earlier job failed (secret) | 1, 3, 5, 10, 25, 50, 100 |
| Click Here to Continue | questions an agent asked a person | 1, 5, 10, 25, 50, 100, 250 |

**Rares.** A few days award a hat instead of a tier: a pull request merged on 25 December gives a party hat (its colour from the character's palette colour), on 31 October a holiday mask, and the first run of a new year a party hat in white. Dates are UTC. A rare goes to the character whose job earned it, once; each drop is a chat-box message ("Valuable drop: Developer received a party hat!").

## Quests and repo diaries

**Quests** are multi-step goals the account completes, each with steps, an optional skill requirement and a reward: quest points for the account, and an XP reward to the characters who did the steps. Like everything else they are recomputed from history. The first set:

| Quest | Steps | Reward |
|---|---|---|
| The Repo's Assistant | a repo onboarded (an `onboard` job completed), then its first job finished, then its first pull request merged | 1 quest point; 40,000 Operations XP to onboarding, 40,000 Coding XP to developer |
| The Restless Build | a job on an issue failed, then a later job on that issue merged | 1 quest point; 60,000 Recovery XP to developer |
| Gatekeeper | a review run, a QA run and a merge on one issue | 2 quest points; 50,000 Reviewing XP to review, 50,000 Quality assurance XP to qa |
| Divine Slayer | requires a character at total level 1,000: 100 merges and a repo diary at Elite | 5 quest points; a Divine trim on every cape |

A quest completes when its last step (and its requirement) is met, and its XP lands then, feeding the levels like any other XP; the journal shows the repo or issue that got furthest. Every quest done gives the account a quest cape, worn by its highest total level unless a skill cape outranks it. The quest list keeps the old colours: red not started, yellow in progress, green complete, with done steps struck through.

**Repo diaries** are an achievement diary per repository, in four tiers:

| Tier | Tasks |
|---|---|
| Easy | an issue triaged, a job finished, a QA run |
| Medium | a pull request merged, a QA pass (a completed QA job, or a merge on an issue QA ran on), a context audit |
| Hard | 10 pull requests merged, a context audit with no outdated claims, 10 reviews |
| Elite | 50 pull requests merged, 30 days without a failed job (a finished job a full 30 days after the repo's last failure, or its first job), every task above |

A tier completes when its last task does. The account page lists the diaries with a task done, furthest along first, each row opening onto its tasks; a tier completed is a chat-box message ("Well done! You have completed the Medium tasks in the <repo> diary.").

## Trust (the last slice; its own spec first)

Level requirements that change what a character may do, like the requirements to wield an item. Examples: a character needs Reviewing 60 before its approval counts toward an automatic merge; Coding 40 before it takes issues marked large. Each requirement is off by default and turned on per organization. Its own spec, written before any code, settles which actions are gated, whether levels count instance-wide or per organization, and how a person overrides a gate.

## Where it shows

- **Account page** (`/achievements`, under Overview): the account XP, the account total and quest points; the achievement diary with tier pips, progress to the next tier, the "What counts" line, and secrets as `???`; the roster of characters with total level and best skill; quests and repo diaries in their slice.
- **A character's skill sheet:** an agent card opens its 23 skills as tiles in the classic three-column grid, grouped Build, Verify, Investigate, Care, each with its level and a bar to the next level.
- **Chat-box messages:** a level gained or an achievement unlocked appears as a short message in the classic chat style, naming the character ("Congratulations, Developer just advanced a Coding level. Its Coding level is now 42."), bottom left, one at a time. The page polls `/progression` every minute; a viewer's last seen time lives in `localStorage`, so each message shows once per browser.
- **Agent cards:** the character's total level and its best skill.
- **What characters wear:** an avatar picks the person and a combat style, and the character climbs that style's ladder from Bronze to Divine. Its tier follows its best skill: Bronze from 1, Iron 15, Steel 30, Mithril 45, Adamant 60, Rune 75, Divine 90.

  | Tier | Melee | Ranged | Magic |
  |---|---|---|---|
  | Bronze to Rune | the metal's open helm, platebody, platelegs, kiteshield, scimitar | leather, hardened, studded, then green, blue and red dragonhide, with a bow from shortbow to magic longbow | wizard, dark, enchanted, mystic, astral and archmage robes, with a staff whose orb brightens |
  | Divine | pale-gold plate | blessed white hide and a crystal bow | celestial robes and a divine staff |

  The helm is open-faced so a character stays recognisable in its gear. From Mithril up a character carries the party cape and an amulet. The monk, the pirate and the two skillers keep their look; only their tool takes the tier's metal. A skill cape in its echo's colours replaces the party cape at 99, with a drape over the shoulders so it shows from the front (the best 99 if several, trimmed for two or more); an earned rare replaces the head item. The twelve 16-pixel avatars could not wear gear, so they became composed 32-pixel people in their class's Bronze kit; their ids stay and an old row still renders. Any sprite of a character the app knows wears its outfit; the agent form's picker shows the avatars as drawn.

## Art

New parts and items (the Divine tier and the class ladders, skill capes, party hats, the holiday mask, the converted characters) are drawn with PixelLab and stored as the roster stores everything: palette-indexed text grids compiled to SVG. A generation takes our current sprite as its starting image and style, and the roster palette as its forced palette; its output is cut into the layer it belongs to and checked in the contact sheet. No image file enters the repository, and the roster directory's source budget rises from 120 KB to 200 KB.

## API

`GET /api/v1/progression` (read tier):

```
{
  account: { xp, total_level, characters, quest_points, divine_trim },
  skills: [{ id, name, group, echo }],
  characters: [{ agent_type, xp, total_level, best_skill, tier, capes: [stat], rares: [{ id, at }], quest_cape,
                 skills: [{ stat, xp, level, level_xp, xp_to_next }] }],
  achievements: [{ id, name, what_counts, secret, state, tier, tiers_reached, value, next_threshold, thresholds, unlocked_at }],
  quests: [{ id, name, points, reward, state, requirement, requirement_met, where, steps: [{ text, done_at }], completed_at }],
  diaries: [{ repo, tiers: [{ tier, tasks: [{ text, done_at }], completed_at }] }],
  recent: [{ kind: "level", at, agent_type, stat, level } | { kind: "rare", at, agent_type, id }
           | { kind: "achievement", at, id, name, tier } | { kind: "quest", at, id, name } | { kind: "diary", at, repo, tier }]
}
```

Computed from the jobs, traces, QA runs, healing cases and audit rows with a few aggregate queries, cached for 60 seconds. One migration: `traces.skill_actions JSONB DEFAULT '{}'`.

## Slices

1. **Characters and the account** (this revision ships with it): the 23 skills, the action classifier and its column, per-character levels and level-ups, the account page and skill sheets, the chat box naming the character, the achievements re-pointed. It replaces the 21-skill model of the first two slices.
2. **Wearing what they earn:** the built-in agents as composed adventurers, the class ladders up to Divine, gear from the best skill, skill capes, rares.
3. **Quests and repo diaries.**
4. **The PixelLab art pass** over parts and items.
5. **Trust:** its spec, then level requirements behind a per-organization flag, off by default.

## Testing

The XP curve against its published points (level 2 at 83, 50 at 101,333, 92 at 6,517,253, 99 at 13,034,431); the classifier on a table of commands and paths; history without `skill_actions` falling back to tool names; each source's query against Postgres with seeded rows; failed, refused and abandoned runs earning nothing; per-character level-ups, and two custom agents levelling apart; unlock times derived from the crossing event; secrets hidden until their first signal; the API shape; the chat-box message naming the character, shown once per browser; every composed character in every state and tier; the roster budget.

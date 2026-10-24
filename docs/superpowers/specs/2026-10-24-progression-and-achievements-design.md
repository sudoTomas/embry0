# Progression and achievements: levels, an achievement diary, and what agents wear

**Goal.** The work embry0 already records becomes experience. Each agent trains a skill, levels from 1 to 99 on the classic experience curve, and the instance has a total level. Achievements, in seven metal tiers from Bronze to Divine, mark what the instance and its agents have done. Unlocks show in four places: an Achievements page, chat-box messages, agent cards, and the agents' sprites, which wear the gear tier, skill cape or rare hat they earned.

The tone is the old-school adventure game the roster already draws (the game UI design, 2026-10-18, and the adventurer roster, 2026-10-20). The reference for achievements is the Hermes Agent achievements plugin (MIT): stable ids, tiered progress, secret entries, a "What counts" line per entry, all recomputed from history.

**Non-goals.** Nothing here changes what an agent does or may do: levels are read, never gates. No leaderboard across deployments, no network calls, no sounds. No trademarked names in code, docs or strings; the art is original pixel art in the style, never copied. No new runtime dependency.

## Words

| Word | Meaning |
|---|---|
| skill | One of the trained abilities below. In code and URLs it is `stat` (`/progression`), because embry0's **Skills** page already means agent skill packs |
| experience (XP) | Points a skill gains from an event the database already records |
| level | 1 to 99 from XP, on the classic curve: `xp(L) = floor(sum_{n=1}^{L-1} floor(n + 300 * 2^(n/7)) / 4)`; level 99 is 13,034,431 XP and level 92 is half of that |
| total level | The sum of every skill's level across the instance |
| tier | Bronze, Iron, Steel, Mithril, Adamant, Rune, Divine: an achievement's seven steps, and the gear an agent wears |

## Skills and what trains them

Every built-in agent type trains one skill, 18 in all, and three instance skills come from work no single agent does; the total level runs to 2,079. The XP of an event is fixed per source, chosen so that the busiest agent of an active deployment reaches 99 in about a year and the first levels come in the first week, as on the classic curve.

| Skill | Trained by | Event (all already in the database) | XP |
|---|---|---|---|
| Smithing | developer | a job's pull request merged (`jobs.status = pr_merged`) | 90,000 |
| Defence | review | a review run that ended without an error | 30,000 |
| Herblore | qa | a QA job that completed | 60,000 |
| Hunter | triage | a triage run that ended without an error | 2,000 |
| Magic | analysis | an analysis run that ended without an error | 2,500 |
| Runecraft | research | a research run that ended without an error | 40,000 |
| Construction | onboarding | an onboarding run that ended without an error | 150,000 |
| Firemaking | ops | an ops run that ended without an error | 40,000 |
| Prayer | pr-reviewer | a pull request review run that ended without an error | 30,000 |
| Fletching | context-auditor | a context audit completed, plus 1,000 per outdated claim found | 40,000 |
| Crafting | design-auditor | a design audit run that ended without an error | 40,000 |
| Hitpoints, Woodcutting, Thieving, Fishing, Agility, Ranged, Attack | kintsugi, gardener, interrogator, why, how, blast-radius, comment-sicko | a run of that specialist that ended without an error | 40,000 |
| Slayer | the watcher (instance) | a healing case verified healed | 120,000 |
| Mining | the watcher (instance) | a watcher run that analysed logs | 3,000 |
| Farming | the schedules (instance) | a context audit the schedule started and the budget gate let run | 4,000 |

A custom agent trains the skill of the built-in it was copied from, else Magic. Failed and refused jobs earn nothing. XP is recomputed from the rows, never stored, so a changed table re-reads the past the same way for everyone.

## Achievements

An achievement is data: `{ id, name, description, what_counts, skill?, metric, thresholds[7], secret?, reward? }`. `id` is stable forever (it keys the unlock); `thresholds` are the metric's value for Bronze through Divine. An achievement is **unlocked** at its first tier, **discovered** with its progress shown while locked, or **secret**, shown as `???` until its first signal. Its unlock time is the time the metric crossed the threshold, so it is derived from history like XP.

The first catalogue (names in the game's voice; the metrics are embry0's):

| Name | What counts | Tiers (Bronze → Divine) |
|---|---|---|
| Fresh off the Tutorial | jobs completed | 1, 10, 50, 100, 250, 500, 1,000 |
| Forged in Fire | pull requests merged | 1, 5, 15, 30, 60, 120, 250 |
| Guardian of the Gate | reviews with a verdict | 1, 10, 25, 50, 100, 250, 500 |
| Potion Master | QA runs passed | 1, 5, 15, 30, 60, 120, 250 |
| Slayer Task Complete | healing cases verified | 1, 3, 10, 25, 50, 100, 200 |
| Lines Straightened | outdated claims a context audit found | 10, 50, 100, 250, 500, 1,000, 2,500 |
| Bank Value | USD spent on jobs | 10, 50, 100, 250, 500, 1,000, 2,500 |
| Tick Perfect | jobs completed in under two minutes | 1, 10, 25, 50, 100, 250, 500 |
| Wise Old Agent | total level | 50, 100, 250, 500, 750, 1,000, 1,250 |
| Skill Cape | skills at 99 | 1 to 7 |
| Oh Dear, You Are Dead | jobs that failed, then a retry that merged (secret) | 1, 5, 10, 25, 50, 100, 250 |
| Click Here to Continue | questions answered for an agent that asked (`awaiting_input`) | 1, 5, 10, 25, 50, 100, 250 |

**Rares.** A few secrets award a hat instead of a tier: a pull request merged on 25 December gives a party hat (its colour from the agent's palette colour), on 31 October a holiday mask, and the first job of a new year a party hat in white. A rare goes to the agent whose job earned it.

## Where it shows

- **Achievements page** (`/achievements`, under Overview): the instance's total level and each skill as a level tile, then the achievements grouped by skill, each with its tier pips (Bronze to Divine), progress to the next tier and its "What counts" line; secrets as `???`. Per-agent pages show the agent's skill, level, progress bar and earned items.
- **Chat-box messages:** a level gained or an achievement unlocked appears as a short message in the classic chat style ("Congratulations, you just advanced a Smithing level. Your Smithing level is now 42."), bottom left, one at a time. The page polls `/progression` every minute; a viewer's last seen time lives in `localStorage`, so each message shows once per browser.
- **Agent cards:** the agent's skill icon and level beside its name.
- **What agents wear:** an agent's gear tier follows its level (Bronze from 1, Iron 15, Steel 30, Mithril 45, Adamant 60, Rune 75, Divine 90: experience piles up at the curve's top, and a few months of an active deployment already reach the 80s), a skill cape replaces its party cape at 99, and an earned rare replaces its head item. The cape keeps the party colour except a skill cape, which has the skill's own.

The legacy 16-pixel avatars cannot wear gear, so the built-in agents move to composed adventurers: smith, warden, alchemist, seer, herald, arbiter, scholar, oracle, engineer, rogue, bard and golem get 32-pixel appearances built from parts and items. Their ids stay; an old row still renders.

## Art

New parts and items (the Divine tier, skill capes, party hats, the holiday mask, the twelve converted characters) are drawn with PixelLab and stored as the roster stores everything: palette-indexed text grids compiled to SVG. A generation takes our current sprite as its starting image and style, and the roster palette as its forced palette; its output is cut into the layer it belongs to and checked in the contact sheet. No image file enters the repository, and the roster directory's source budget rises from 120 KB to 200 KB.

## API

`GET /api/v1/progression` (read tier): `{ total_level, total_xp, stats: [{ stat, xp, level, xp_to_next, trained_by }], agents: [{ agent_type, stat, xp, level, tier, cape, rare }], achievements: [{ id, name, what_counts, tier, progress, next_threshold, state, unlocked_at }], recent: [{ kind, at, ... }] }`. Computed from the jobs, traces, QA runs, healing cases and audit rows with a few aggregate queries, cached for 60 seconds. No migration.

## Slices

1. The progression engine, its API, and levels on agent cards (this spec ships with it).
2. The achievements catalogue, the Achievements page and the chat-box messages.
3. The built-in agents as composed adventurers, the Divine tier, skill capes and rares, worn from progression.
4. The PixelLab art pass over parts and items.

## Testing

The XP curve against its published points (level 2 at 83, 50 at 101,333, 92 at 6,517,253, 99 at 13,034,431); each source's query against Postgres with seeded rows; failed, refused and abandoned jobs earning nothing; unlock times derived from the crossing event; secrets hidden until their first signal; the API shape; the chat-box message shown once per browser; every composed character in every state and tier; the roster budget.

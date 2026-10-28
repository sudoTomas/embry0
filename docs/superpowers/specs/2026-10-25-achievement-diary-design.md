# The achievement diary: outcomes, not activity, on the Hermes achievements layout

**Why.** The first catalogue (spec 2026-10-24) had fourteen entries, thirteen already unlocked, and several of them rewarded noise: money spent, watcher runs, jobs finished (mostly log analyses), jobs that ended within fifteen seconds. Its page showed seven unlabelled coloured squares per card, so nobody could tell what the colours meant, and its names did not say what they counted. This rebuilds both.

**What changes.**
- **Outcomes only.** Every metric is something that helped: a pull request that merged (and how: first try, without an error, cheaply, quickly, with review and QA), a production error found healed or that never came back, an issue finished, an owner's answer, outdated claims found and corrected, a change the verification gate stopped, a comeback, a character's growth. Nothing counts spend, runs or jobs started.
- **Fifty entries in eight categories**, nine of them secret. Most climb the seven tiers, Bronze, Iron, Steel, Mithril, Adamant, Rune and Divine; a few are one-off objectives that read *Complete*.
- **Ids are kept** where the metric stayed (`forged_in_fire`, `guardian_of_the_gate`, `potion_master`, `hunt_complete`, `lines_straightened`, `elder_agent`, `mastery_cloak`, `back_on_your_feet`); a display name may change, an id never does. The activity entries are retired with their ids (`fresh_off_the_tutorial`, `prospector`, `green_thumb`, `bank_value`, `tick_perfect`, `click_here_to_continue`, which counted questions asked and is now `answer_given`, questions answered).
- **Evidence.** Each step of a metric carries what did it (a pull request, a repository, an error's title, a link when there is one); the step that last moved an achievement is its evidence.
- **Names** keep the old game's voice but say what they count: *Gz on the Merge*, *No-Death Run*, *Merged for 1gp*, *Safespotted*, *You Shall Not Merge*.

## How it is computed

As before, nothing is stored. `ProgressionRepository.facts()` reads one row per step of every metric, with its label and link, from the jobs, traces, healing cases and their events, issues, owner answers, agent questions, PR reviews, test-setup proposals and standards state. A merged pull request is one the forge confirmed (`delivery_state = 'merged'`) or whose job says so. The skilling metrics come from the level-ups, agent levels and rare drops progression computes anyway. `services/achievements.evaluate` adds the steps up per metric, records when each threshold was crossed, and keeps the last step as the evidence. The API adds `category`, `kind`, `icon`, `flavour`, `latest_at` and `evidence` to each entry, and `achievement_categories` to the response.

## The page

In the Hermes achievements plugin's order (MIT; its README and screenshots are the reference), in embry0's own pixel style:
1. A hero: the title and one line on what the diary is.
2. Five tiles: unlocked of all, discovered, secrets, the highest tier reached, the latest unlock.
3. The tier legend, every tier named in its colour in order, and a short note on secrets.
4. Category chips and a state filter (all, unlocked, discovered, secret), kept in the address (`?category=`, `?state=`).
5. Recent unlocks, one chip per achievement.
6. One card per achievement: an original 12×12 pixel icon in the tier's metal, the name, the category, a state chip and a tier chip in words (or the tier it is working towards), the flavour line, a collapsible *What counts* with every threshold, the evidence, and a bar to the next tier with the numbers.

The account's summary, quests, repo diaries, roster and skill sheet stay below it, under *The account*. The chat box says "completed the X achievement" for an objective.

## The catalogue

### Shipping

Pull requests that merged, and how cleanly they got there.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `forged_in_fire` | Gz on the Merge | Pull requests embry0 opened that merged | 1, 5, 15, 30, 60, 120, 250 |
| `no_death_run` | No-Death Run | Merged pull requests whose issue or pull request had no earlier attempt | 1, 5, 10, 25, 50, 100, 200 |
| `clean_kill` | Flawless Kill | Merged pull requests whose job never had an agent run end in an error | 1, 5, 10, 25, 50, 100, 200 |
| `merged_for_a_penny` | Merged for 1gp | Merged pull requests whose job spent under one US dollar | 1, 3, 10, 25, 50, 100, 200 |
| `speedrun_any_percent` | Speedrun Any% | Pull requests merged within a day of their job starting | 1, 5, 10, 25, 50, 100, 200 |
| `repo_hopper` | World Hopper | Repositories with at least one merged pull request | 1, 2, 3, 5, 8, 13, 21 |
| `know_your_zones` | Know Your Zones | Merged pull requests that named the area of the monorepo they changed | 1, 5, 10, 25, 50, 100, 200 |
| `full_raid_party` | Full Raid Party | Merged pull requests whose job passed both a review and a QA run | 1, 5, 10, 25, 50, 100, 200 |
| `hot_streak` | Hot Streak | Days with three merged pull requests or more | 1, 3, 5, 10, 20, 40, 80 |

### Slayer

Production errors the watcher found, and the fixes that kept them dead.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `hunt_complete` | Slayer Task Complete | Production errors whose fix was checked against the logs afterwards and found healed | 1, 3, 10, 25, 50, 100, 200 |
| `out_of_reach` | Safespotted | Production errors the watcher found itself that embry0's pull request then fixed | 1, 3, 10, 25, 50, 100, 200 |
| `on_the_hunt` | Task Assigned | Watcher findings a person accepted for fixing | 1, 5, 10, 25, 50, 100, 200 |
| `boss_hunter` | Boss Slayer | High-severity production errors found healed | 1, 2, 5, 10, 25, 50, 100 |
| `elite_hunter` | Superior Slayer | Healed production errors that had been seen ten times or more | 1, 2, 5, 10, 25, 50, 100 |
| `never_respawned` | Never Respawned | Healed production errors that never came back after their fix was checked | 1, 3, 10, 25, 50, 100, 200 |
| `deployed_and_dusted` | Deployed and Dusted | Fixes for production errors that reached a deploy | 1, 5, 10, 25, 50, 100, 200 |

### Questing

Issues finished, and the answers owners gave.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `quest_complete` | Quest Complete! | Issues finished by a merged pull request | 1, 5, 15, 30, 60, 120, 250 |
| `side_quest` | Side Quest | Sub-issues triage split off a bigger issue that a merged pull request finished | 1, 5, 10, 25, 50, 100, 200 |
| `talked_to_the_quest_giver` | Talked to the Quest Giver | Answers owners gave to embry0's proposals: start it, not this, or later | 1, 5, 10, 25, 50, 100, 250 |
| `quest_accepted` | Quest Accepted | Proposals an owner said yes to | 1, 5, 10, 25, 50, 100, 200 |
| `reward_claimed` | Reward Claimed | Accepted proposals whose job's pull request merged | 1, 5, 10, 25, 50, 100, 200 |
| `answer_given` | Click Here to Continue | Questions an agent asked that a person answered | 1, 5, 10, 25, 50, 100, 250 |

### Lore

Instruction files, area maps and standards kept true.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `lines_straightened` | Lines Straightened | Outdated claims the context audit found in the files that steer coding agents | 10, 50, 100, 250, 500, 1,000, 2,500 |
| `lore_corrected` | Lore Corrected | Context-audit pull requests that merged | 1, 5, 10, 25, 50, 100, 200 |
| `weeded_garden` | Weeded Garden | Context audits that found every instruction file already true | 1, 5, 10, 25, 50, 100, 200 |
| `cartographer` | Cartographer | Repositories whose areas map merged | 1, 2, 3, 5, 8, 13, 21 |
| `fashion_inspector` | Fashion Inspector | Repositories whose design audit scored 90 or more against their standard | 1, 2, 3, 5, 8, 13, 21 |

### Guarding

Reviews, QA and the gate that stops a bad merge.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `guardian_of_the_gate` | Guardian of the Gate | Reviews a reviewer agent finished | 1, 10, 25, 50, 100, 250, 500 |
| `second_opinion` | Second Opinion | Pull request reviews embry0 posted on a pull request | 1, 10, 25, 50, 100, 250, 500 |
| `potion_master` | Potion Master | QA runs that completed | 1, 5, 15, 30, 60, 120, 250 |
| `you_shall_not_merge` | You Shall Not Merge | Changes the verification gate stopped because they broke tests that passed before | 1, 2, 5, 10, 25, 50, 100 |
| `training_grounds` | Training Grounds | Test setups embry0 proposed that a person applied to a repository | 1, 2, 3, 5, 8, 13, 21 |

### Grit

Work that failed, came back, and landed anyway.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `back_on_your_feet` | Oh Dear, You Are Dead (secret) | Merges on an issue whose earlier job failed | 1, 3, 5, 10, 25, 50, 100 |
| `back_from_the_respawn` | Back from the Respawn | Jobs a restart interrupted that resumed and finished their work | 1, 3, 5, 10, 25, 50, 100 |
| `lag_survivor` | Lag Survivor (secret) | A job interrupted twice or more that still finished its work | objective |
| `second_wind` | Second Wind | Follow-up jobs on a pull request whose pull request then merged | 1, 2, 5, 10, 25, 50, 100 |
| `iron_will` | Iron Will | Issues that merged on their third attempt or later | 1, 2, 3, 5, 10, 25, 50 |

### Skilling

What the characters grew into.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `elder_agent` | Wise Old Agent | The highest total level of any one character | 100, 250, 500, 750, 1,000, 1,500, 2,000 |
| `mastery_cloak` | Skill Cape | Skills at 99 across every character | 1, 2, 5, 10, 23, 50, 100 |
| `fully_trained` | Maxed Main | The highest level any one character reached | 10, 25, 50, 75, 100, 115, 126 |
| `halfway_by_experience` | 92 Is Half of 99 | Skills that reached level 92, across every character | 1, 2, 5, 10, 23, 50, 100 |
| `thousand_club` | Thousand Club | Characters whose total level reached 1,000 | 1, 2, 3, 5, 8, 12, 18 |
| `rare_finds` | Rare Drop Table | Rare drops characters earned | 1, 2, 3, 5, 8, 13, 21 |

### Oddities

Strange hours and rare finds. Mostly secret.

| Id | Name | What counts | Thresholds |
|---|---|---|---|
| `afk_weekend` | AFK Weekend (secret) | A pull request that merged on a Saturday or Sunday (UTC) | objective |
| `night_shift` | Night Shift (secret) | A pull request that merged between midnight and four in the morning (UTC) | objective |
| `friday_night_merge` | Friday Night Merge (secret) | A pull request that merged on a Friday after eight in the evening (UTC) | objective |
| `holiday_event` | Holiday Event (secret) | A pull request that merged on 25 December, 31 October or 1 January (UTC) | objective |
| `long_grind` | Long Grind (secret) | A pull request that merged from a job that ran for more than two hours | objective |
| `lucky_find` | Pet Drop (secret) | A pull request that merged from the first job embry0 ever ran on its repository | objective |
| `ninety_nine_problems` | 99 Problems (secret) | A production error seen 99 times or more that a merged fix killed | objective |

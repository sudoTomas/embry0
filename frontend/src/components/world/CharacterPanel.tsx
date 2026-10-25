import { Link } from "react-router";
import type { Party, QuestLogEntry, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useCharacter } from "@/hooks/useWorld";
import { TIER_NAME } from "@/lib/tiers";
import {
  OUTCOME_WORDS,
  STATE_POSE,
  STATE_WORDS,
  bestSkills,
  formatCost,
  formatPercent,
  formatSeconds,
  outcomeBadgeTone,
  placeOf,
  stateTone,
} from "@/lib/world";
import { formatDate } from "@/lib/utils";
import type { Tier } from "@/api/progression";
import { WatchParty } from "./WatchParty";

interface CharacterPanelProps {
  character: WorldCharacter;
  party: Party | undefined;
  skillName: (stat: string) => string;
  canAct: boolean;
  onClose: () => void;
  onOpenQuest: (jobId: string) => void;
  onWriteQuest: (agentType: string) => void;
}

const RATING_WORDS = { good: "Good", needs_work: "Needs work", bad: "Bad" } as const;

function LogRow({ entry, onOpen }: { entry: QuestLogEntry; onOpen: () => void }) {
  return (
    <tr className="cursor-pointer border-t border-border hover:bg-overlay" onClick={onOpen} data-testid="quest-log-row">
      <td className="max-w-[14rem] truncate py-1.5 pr-2 text-fg" title={entry.title}>
        {entry.title}
      </td>
      <td className="hidden py-1.5 pr-2 text-fg-muted sm:table-cell">{placeOf(entry.repo, entry.area)}</td>
      <td className="py-1.5 pr-2">
        <Badge tone={outcomeBadgeTone(entry.outcome)}>{OUTCOME_WORDS[entry.outcome]}</Badge>
      </td>
      <td className="hidden py-1.5 pr-2 text-right tabular-nums text-fg-muted sm:table-cell">{formatCost(entry.cost_usd)}</td>
      <td className="hidden py-1.5 pr-2 text-right tabular-nums text-fg-muted sm:table-cell">{formatSeconds(entry.seconds)}</td>
      <td className="py-1.5 text-fg-muted">{entry.rating ? RATING_WORDS[entry.rating] : entry.verdict ? entry.verdict.replace("_", " ") : ""}</td>
    </tr>
  );
}

/** One agent up close: its gear and skills, what it is doing (watched live while it works), its record and its job history. */
export function CharacterPanel({ character, party, skillName, canAct, onClose, onOpenQuest, onWriteQuest }: CharacterPanelProps) {
  const identity = useIdentityLookup()(character.agent_type);
  const detail = useCharacter(character.agent_type);
  const stats = character.stats;
  const out = party && ["travelling", "working", "waiting"].includes(party.state);
  const rated = stats.ratings.good + stats.ratings.needs_work + stats.ratings.bad;
  return (
    <Dialog onClose={onClose} side="right" title={identity.displayName} className="max-w-xl">
      <div className="space-y-5 p-5" data-testid="character-panel">
        <div className="flex items-start gap-4">
          <AgentAvatar identity={identity} size={64} state={STATE_POSE[character.state]} />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm text-fg-secondary">
              <span className="hud text-fg">Level {character.level}</span> · total {character.total_level} · {TIER_NAME[character.tier as Tier] ?? character.tier}
            </p>
            <p className="text-xs text-fg-muted">{bestSkills(character, 5).map((s) => `${skillName(s.stat)} ${s.level}`).join(" · ")}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2" data-testid="character-doing">
          <Badge tone={stateTone(character.state)}>{STATE_WORDS[character.state]}</Badge>
          {party && <span className="text-sm text-fg-secondary">{`${party.title} · ${placeOf(party.repo, party.area)}`}</span>}
        </div>
        {out && party && <WatchParty party={party} canAct={canAct} />}
        {party && !out && (
          <Button variant="outline" size="sm" onClick={() => onOpenQuest(party.job_id)}>
            Open its report
          </Button>
        )}

        <dl className="grid grid-cols-3 gap-2" data-testid="character-stats">
          {[
            ["Jobs", String(stats.quests)],
            ["Succeeded", formatPercent(stats.success_rate)],
            ["Merged", String(stats.merged)],
            ["Avg cost", formatCost(stats.avg_cost_usd)],
            ["Avg time", formatSeconds(stats.avg_seconds)],
            ["Rated good", `${stats.ratings.good}/${rated}`],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-border px-2.5 py-2">
              <dt className="text-2xs text-fg-muted">{label}</dt>
              <dd className="text-sm font-semibold tabular-nums text-fg">{value}</dd>
            </div>
          ))}
        </dl>

        <div className="flex flex-wrap gap-2">
          {character.can_quest && (
            <Button size="sm" disabled={!canAct} onClick={() => onWriteQuest(character.agent_type)}>
              New task
            </Button>
          )}
          <Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/agents/${encodeURIComponent(character.agent_type)}`}>
            Agent settings
          </Link>
        </div>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Job history</h3>
          {detail.isLoading ? (
            <p className="text-xs text-fg-muted">Loading…</p>
          ) : !detail.data?.quest_log.length ? (
            <p className="text-xs text-fg-muted">No jobs yet.</p>
          ) : (
            <table className="w-full text-xs" data-testid="quest-log">
              <tbody>
                {detail.data.quest_log.map((entry) => (
                  <LogRow key={entry.job_id} entry={entry} onOpen={() => onOpenQuest(entry.job_id)} />
                ))}
              </tbody>
            </table>
          )}
          {stats.last_quest_at && <p className="text-2xs text-fg-muted">Last job {formatDate(stats.last_quest_at)}</p>}
        </section>
      </div>
    </Dialog>
  );
}

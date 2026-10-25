import { Link } from "react-router";
import type { Party, QuestLogEntry, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useCharacter } from "@/hooks/useWorld";
import { OUTCOME_WORDS, STATE_POSE, STATE_WORDS, bestSkills, formatCost, formatPercent, formatSeconds, outcomeTone, placeOf } from "@/lib/world";
import { formatDate } from "@/lib/utils";
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

const RATING_WORDS = { good: "good", needs_work: "needs work", bad: "bad" } as const;

function LogRow({ entry, onOpen }: { entry: QuestLogEntry; onOpen: () => void }) {
  const tone = outcomeTone(entry.outcome);
  return (
    <tr className="cursor-pointer hover:bg-white/5" onClick={onOpen} data-testid="quest-log-row">
      <td className="max-w-[14rem] truncate py-1 pr-2 world-text" title={entry.title}>
        {entry.title}
      </td>
      <td className="hidden py-1 pr-2 world-muted sm:table-cell">{placeOf(entry.repo, entry.area)}</td>
      <td className="py-1 pr-2" style={{ color: tone === "win" ? "var(--win)" : tone === "loss" ? "var(--loss)" : undefined }}>
        {OUTCOME_WORDS[entry.outcome]}
      </td>
      <td className="hidden py-1 pr-2 text-right world-muted sm:table-cell">{formatCost(entry.cost_usd)}</td>
      <td className="hidden py-1 pr-2 text-right world-muted sm:table-cell">{formatSeconds(entry.seconds)}</td>
      <td className="py-1 world-muted">{entry.rating ? RATING_WORDS[entry.rating] : entry.verdict ? entry.verdict.replace("_", " ") : ""}</td>
    </tr>
  );
}

/** One character up close: its gear and skills, what it is doing, its totals, its quest log; watch it, recall it, give it a quest. */
export function CharacterPanel({ character, party, skillName, canAct, onClose, onOpenQuest, onWriteQuest }: CharacterPanelProps) {
  const identity = useIdentityLookup()(character.agent_type);
  const detail = useCharacter(character.agent_type);
  const stats = character.stats;
  const out = party && ["travelling", "working", "waiting"].includes(party.state);
  return (
    <Dialog onClose={onClose} side="right" label={identity.displayName} className="world max-w-xl">
      <div className="world-panel flex h-full flex-col gap-3 overflow-y-auto p-4" data-testid="character-panel">
        <div className="flex items-start gap-3">
          <AgentAvatar identity={identity} size={64} state={STATE_POSE[character.state]} />
          <div className="min-w-0 flex-1">
            <h2 className="world-title text-lg">{identity.displayName}</h2>
            <p className="world-subtitle text-xs">
              Level {character.level} · total {character.total_level} · {character.tier} gear
            </p>
            <p className="world-muted text-xs">{bestSkills(character, 5).map((s) => `${skillName(s.stat)} ${s.level}`).join(" · ")}</p>
          </div>
          <button type="button" className="world-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <p className="world-text text-sm" data-testid="character-doing">
          {STATE_WORDS[character.state]}
          {party ? `: ${party.title} · ${placeOf(party.repo, party.area)}` : "."}
        </p>
        {out && party && <WatchParty party={party} canAct={canAct} />}
        {party && !out && (
          <button type="button" className="world-button self-start" onClick={() => onOpenQuest(party.job_id)}>
            Read its quest scroll
          </button>
        )}

        <dl className="grid grid-cols-3 gap-2 text-center" data-testid="character-stats">
          {[
            ["Quests", String(stats.quests)],
            ["Won", formatPercent(stats.success_rate)],
            ["Merged", String(stats.merged)],
            ["Avg cost", formatCost(stats.avg_cost_usd)],
            ["Avg time", formatSeconds(stats.avg_seconds)],
            ["Rated good", `${stats.ratings.good}/${stats.ratings.good + stats.ratings.needs_work + stats.ratings.bad}`],
          ].map(([label, value]) => (
            <div key={label} className="world-slot rounded-sm p-1.5">
              <dt className="world-muted text-[10px] uppercase">{label}</dt>
              <dd className="world-title text-sm">{value}</dd>
            </div>
          ))}
        </dl>

        <div className="flex flex-wrap gap-1.5">
          {character.can_quest && (
            <button type="button" className="world-button" data-tone="win" disabled={!canAct} onClick={() => onWriteQuest(character.agent_type)}>
              Give it a quest
            </button>
          )}
          <Link className="world-button" to={`/agents/${encodeURIComponent(character.agent_type)}`}>
            Character sheet
          </Link>
        </div>

        <section className="min-h-0">
          <h3 className="world-subtitle mb-1 text-sm">Quest log</h3>
          {detail.isLoading ? (
            <p className="world-muted text-xs">Reading the log…</p>
          ) : !detail.data?.quest_log.length ? (
            <p className="world-muted text-xs">No quests yet.</p>
          ) : (
            <table className="w-full text-[11px]" data-testid="quest-log">
              <tbody>
                {detail.data.quest_log.map((entry) => (
                  <LogRow key={entry.job_id} entry={entry} onOpen={() => onOpenQuest(entry.job_id)} />
                ))}
              </tbody>
            </table>
          )}
          {stats.last_quest_at && <p className="world-muted mt-1 text-[10px]">Last out {formatDate(stats.last_quest_at)}</p>}
        </section>
      </div>
    </Dialog>
  );
}

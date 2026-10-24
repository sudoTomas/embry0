import { useEffect, useState } from "react";
import type { AgentState } from "@/hooks/useAgentStates";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useOutfit } from "@/components/progression/outfits";
import { Sprite, stateFor } from "@/components/roster";
import { motionIndex } from "@/lib/motion";

interface PartyStripProps {
  /** Runs in order (useAgentStates: started runs first, then the graph's pending nodes). */
  agents: AgentState[];
  selectedRunId: string | null;
  onSelect: (runId: string | null) => void;
}

/**
 * A member's turn to arrive is fixed when it mounts: in its turn on the
 * strip's first paint, at once when it joins later. The entrance is the
 * button's (.motion-enter); the sprite inside keeps its one animation.
 */
function useArrivalTurn(turn: number): number {
  const [i] = useState(turn);
  return i;
}

function Member({
  agent,
  turn,
  selected,
  onSelect,
}: {
  agent: AgentState;
  turn: number;
  selected: boolean;
  onSelect: (runId: string | null) => void;
}) {
  const identityFor = useIdentityLookup();
  const identity = identityFor(agent.agent);
  const outfit = useOutfit(agent.agent);
  const state = stateFor(agent);
  const i = useArrivalTurn(turn);
  return (
    <button
      type="button"
      data-run-id={agent.runId}
      data-state={state}
      aria-pressed={selected}
      title={agent.retryLabel ? `${identity.displayName} (${agent.retryLabel})` : identity.displayName}
      onClick={() => onSelect(selected ? null : agent.runId)}
      className={`motion-enter flex shrink-0 flex-col items-center gap-1 rounded-md px-2 py-1.5 w-[72px] transition-colors ${
        selected ? "bg-card ring-1" : "hover:bg-card/60"
      } ${agent.status === "pending" ? "opacity-70" : ""}`}
      style={{ ...motionIndex(i), ...(selected ? { boxShadow: `0 0 0 1px ${identity.hex}` } : {}) }}
    >
      <Sprite avatar={identity.avatar} color={identity.color} state={state} size={32} decorative outfit={outfit} />
      <span className="sprite-nameplate truncate w-full text-center" style={{ color: identity.hex }}>
        {identity.displayName}
      </span>
    </button>
  );
}

/**
 * The party strip (spec 3.5): one 32 px sprite per agent run, in run order
 * and in its state, the display name underneath. Clicking a run selects it
 * for the transcript and the map; clicking it again clears the selection.
 * Scrolls horizontally on narrow screens. The members arrive in turn on the
 * first paint; a run that joins later arrives at once.
 */
export function PartyStrip({ agents, selectedRunId, onSelect }: PartyStripProps) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (agents.length > 0) setShown(true);
  }, [agents.length]);
  if (agents.length === 0) return null;
  return (
    <div data-testid="party-strip" role="group" aria-label="Party" className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
      {agents.map((agent, index) => (
        <Member key={agent.runId} agent={agent} turn={shown ? 0 : index} selected={agent.runId === selectedRunId} onSelect={onSelect} />
      ))}
    </div>
  );
}

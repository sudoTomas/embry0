import type { AgentState } from "@/hooks/useAgentStates";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { Sprite, stateFor } from "@/components/roster";

interface PartyStripProps {
  /** Runs in order (useAgentStates: started runs first, then the graph's pending nodes). */
  agents: AgentState[];
  selectedRunId: string | null;
  onSelect: (runId: string | null) => void;
}

/**
 * The party strip (spec 3.5): one 32 px sprite per agent run, in run order
 * and in its state, the display name underneath. Clicking a run selects it
 * for the transcript and the map; clicking it again clears the selection.
 * Scrolls horizontally on narrow screens.
 */
export function PartyStrip({ agents, selectedRunId, onSelect }: PartyStripProps) {
  const identityFor = useIdentityLookup();
  if (agents.length === 0) return null;
  return (
    <div data-testid="party-strip" role="group" aria-label="Party" className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1">
      {agents.map((agent) => {
        const identity = identityFor(agent.agent);
        const state = stateFor(agent);
        const selected = agent.runId === selectedRunId;
        return (
          <button
            key={agent.runId}
            type="button"
            data-run-id={agent.runId}
            data-state={state}
            aria-pressed={selected}
            title={agent.retryLabel ? `${identity.displayName} (${agent.retryLabel})` : identity.displayName}
            onClick={() => onSelect(selected ? null : agent.runId)}
            className={`flex shrink-0 flex-col items-center gap-1 rounded-md px-2 py-1.5 w-[72px] transition-colors ${
              selected ? "bg-card ring-1" : "hover:bg-card/60"
            } ${agent.status === "pending" ? "opacity-70" : ""}`}
            style={selected ? { boxShadow: `0 0 0 1px ${identity.hex}` } : undefined}
          >
            <Sprite avatar={identity.avatar} color={identity.color} state={state} size={32} decorative />
            <span className="sprite-nameplate truncate w-full text-center text-[11px]" style={{ color: identity.hex }}>
              {identity.displayName}
            </span>
          </button>
        );
      })}
    </div>
  );
}

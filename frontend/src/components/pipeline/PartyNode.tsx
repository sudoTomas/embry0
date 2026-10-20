import type { CSSProperties } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Sprite, stateForNode } from "@/components/roster";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { useAgents } from "@/hooks/useAgents";
import { resolveAgentType } from "@/lib/agentIdentity";
import type { NodeLiveState } from "@/lib/pipeline-live";
import { effectiveSkills, type NormalizedDelta } from "@/lib/skills";
import type { AgentDefinition } from "@/lib/types/agents";
import "./party.css";

const TILE_PX = 96;

/**
 * The one agent node (spec 3.4): a 96 px tile with the agent's sprite in the
 * state's frame, a ring in its palette colour, the display name on a pixel
 * nameplate and one HUD line. Editor mode (data.mode absent) shows the
 * effective skill count and the model; live mode shows attempt, cost and
 * turns and maps the node's live state onto the sprite and the ring. The
 * start and end sentinels stay on StartEndNode.
 */
export function PartyNode({ data, selected }: NodeProps) {
  const agentType = data.agentType as string;
  const label = data.label as string | undefined;
  const identity = useAgentIdentity(agentType);
  const { data: agents } = useAgents();
  const mode = data.mode === "live" ? "live" : "editor";
  const liveState = data.state as NodeLiveState["state"] | undefined;
  const spriteState = mode === "live" ? stateForNode(liveState) : "idle";
  const ring = mode === "live" ? (liveState === "ready" ? "pending" : (liveState ?? "pending")) : "editor";

  let hud: string;
  if (mode === "live") {
    const cost = (data.costUsd as number | undefined) ?? 0;
    hud = `#${(data.iteration as number | undefined) ?? 1} · $${cost.toFixed(2)} · ${(data.turns as number | undefined) ?? 0} turns`;
  } else {
    const row = (agents as AgentDefinition[] | undefined)?.find((a) => a.type === resolveAgentType(agentType));
    const skills = effectiveSkills(row?.skills ?? [], data.skills as NormalizedDelta | null).length;
    const model = data.model as string | null | undefined;
    const shortModel = model ? model.replace("claude-", "").split("-")[0] : "defaults";
    hud = `${skills} skill${skills === 1 ? "" : "s"} · ${shortModel}`;
  }

  return (
    <div data-testid="party-node" data-mode={mode} className="flex flex-col items-center gap-1 select-none">
      <div
        className="relative transition-transform duration-200 hover:-translate-y-0.5"
        style={{ width: TILE_PX, height: TILE_PX, filter: selected ? `drop-shadow(0 0 12px ${identity.hex}66)` : undefined }}
      >
        <Handle type="target" position={Position.Left} className="!w-2 !h-2 !bg-card" style={{ border: `2px solid ${identity.hex}`, top: "50%" }} />
        <div className="party-tile absolute inset-1 flex items-center justify-center bg-card">
          <Sprite avatar={identity.avatar} color={identity.color} state={spriteState} size={48} label={`${identity.displayName}, ${spriteState}`} />
        </div>
        <div data-ring={ring} aria-hidden="true" className="party-ring absolute inset-1" style={{ "--party-colour": identity.hex } as CSSProperties} />
        <Handle type="source" position={Position.Right} className="!w-2 !h-2 !bg-card" style={{ border: `2px solid ${identity.hex}`, top: "50%" }} />
      </div>
      <div className="flex flex-col items-center min-w-0 max-w-[140px]">
        <div className="sprite-nameplate truncate w-full text-center" style={{ color: identity.hex }}>{identity.displayName}</div>
        {label && label !== identity.displayName && label !== agentType && (
          <div className="text-[11px] text-foreground/80 truncate w-full text-center">{label}</div>
        )}
        <div className="hud text-[10px] text-muted-foreground truncate w-full text-center">{hud}</div>
      </div>
    </div>
  );
}

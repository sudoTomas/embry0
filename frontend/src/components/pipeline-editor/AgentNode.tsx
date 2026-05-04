import { Handle, Position, type NodeProps } from "@xyflow/react";
import { getAgentColor, getAgentCategory } from "@/lib/graph-utils";
import { AlchemicalSigil } from "@/components/divine/AlchemicalSigil";
import { categoryToStage } from "@/lib/sigils";

const SPHERE_PX = 96;
const HALO_RADIUS = 30; // SVG units, viewBox 0..64

/**
 * Spherical agent node — geodesic-identity primitive scaled up to a 96px
 * pipeline-editor node. Outer ring takes the agent category color at 50%;
 * the inner geodesic sigil stays on text-primary (gold) so the system's
 * signature shows on every node while the operator still gets an
 * at-a-glance category cue from the surrounding ring.
 *
 * See `docs/superpowers/specs/2026-05-04-auto-arrange-circular-design.md` §3.4.
 */
export function AgentNode({ data, selected }: NodeProps) {
  const agentType = data.agentType as string;
  const color = getAgentColor(agentType);
  const category = getAgentCategory(agentType);
  const label = (data.label as string) || agentType;
  const tools = data.tools as string[] | undefined;
  const model = data.model as string | undefined;
  const budget = data.maxBudgetUsd as number | undefined;
  // Resolve directly from agentType — categoryToStage handles aliases
  // (developer→develop, reviewer/validator→validate, etc.). Going through
  // getAgentCategory() returns Title-cased "Development" which has no
  // matching stage entry.
  const stage = categoryToStage(agentType);

  const shortModel = model?.replace("claude-", "").split("-")[0] ?? null;
  const ringOpacity = selected ? "0.95" : "0.55";

  return (
    <div className="flex flex-col items-center gap-1.5 select-none">
      <div
        className="relative transition-all duration-200 hover:-translate-y-0.5"
        style={{
          width: SPHERE_PX,
          height: SPHERE_PX,
          filter: selected ? `drop-shadow(0 0 12px ${color}66)` : undefined,
        }}
      >
        <Handle
          type="target"
          position={Position.Left}
          className="!w-2 !h-2 !bg-[#0f1419]"
          style={{ border: `2px solid ${color}`, top: "50%" }}
        />

        {/* Category-colored outer halo ring */}
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox="0 0 64 64"
        >
          <circle
            cx="32"
            cy="32"
            r={HALO_RADIUS}
            fill="none"
            stroke={color}
            strokeWidth="2"
            opacity={ringOpacity}
          />
        </svg>

        {/* Gold geodesic sigil — hemisphere-lit by stage */}
        <div className="absolute inset-2 flex items-center justify-center text-primary">
          {stage ? (
            <AlchemicalSigil stage={stage} size={SPHERE_PX - 16} title={stage} />
          ) : (
            <span
              className="font-display font-bold uppercase tracking-wider"
              style={{ color, fontSize: 20 }}
            >
              {agentType.charAt(0)}
            </span>
          )}
        </div>

        {/* Tool badge — top-right corner */}
        {tools && tools.length > 0 && (
          <span
            className="absolute -top-1 -right-1 text-[9px] bg-[#0f1419] rounded-full w-5 h-5 flex items-center justify-center text-white/60 border"
            style={{ borderColor: `${color}80` }}
            title={`${tools.length} tools`}
          >
            {tools.length}
          </span>
        )}

        <Handle
          type="source"
          position={Position.Right}
          className="!w-2 !h-2 !bg-[#0f1419]"
          style={{ border: `2px solid ${color}`, top: "50%" }}
        />
      </div>

      {/* Label cluster below the sphere */}
      <div className="flex flex-col items-center min-w-0 max-w-[140px]">
        <div
          className="text-[9px] uppercase tracking-widest font-semibold truncate w-full text-center"
          style={{ color: `${color}BB` }}
        >
          {category}
        </div>
        <div className="text-[12px] font-bold text-slate-100 truncate w-full text-center">
          {label}
        </div>
        <div className="flex items-center gap-1 text-[9px] text-white/35 truncate w-full justify-center">
          {shortModel && <span>{shortModel}</span>}
          {shortModel && budget && <span>·</span>}
          {budget && <span>${budget}</span>}
          {!shortModel && !budget && <span>defaults</span>}
        </div>
      </div>
    </div>
  );
}

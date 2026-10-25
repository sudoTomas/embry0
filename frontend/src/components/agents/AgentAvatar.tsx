import { useOutfit } from "@/components/progression/outfits";
import { Sprite, type SpriteState } from "@/components/roster";
import type { AgentIdentity } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";

interface AgentAvatarProps { identity: AgentIdentity; size?: number; state?: SpriteState; still?: boolean; walking?: boolean; className?: string }

const SIZES = [64, 48, 32, 16] as const;

/**
 * The agent's sprite (spec 3.3), wearing what the character earned
 * (progression spec 2026-10-24). The wrapper keeps the data attributes and
 * title workstream 2 callers and tests read.
 */
export function AgentAvatar({ identity, size = 32, state = "idle", still, walking, className }: AgentAvatarProps) {
  const spriteSize = SIZES.find((s) => s <= size) ?? 16;
  const outfit = useOutfit(identity.type);
  return (
    <span
      data-avatar={identity.avatar}
      data-color={identity.color}
      title={identity.displayName}
      className={cn("inline-flex shrink-0 items-center justify-center select-none", className)}
      style={{ width: size, height: size }}
    >
      <Sprite avatar={identity.avatar} color={identity.color} state={state} size={spriteSize} still={still} walking={walking} label={identity.displayName} outfit={outfit} />
    </span>
  );
}

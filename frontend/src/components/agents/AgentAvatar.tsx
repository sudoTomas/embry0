import { cn } from "@/lib/utils";
import type { AgentIdentity } from "@/lib/agentIdentity";

interface AgentAvatarProps { identity: AgentIdentity; size?: number; className?: string }

/** Tinted square with the display name's initial. Workstream 3 swaps the body for a sprite; the
 * data attributes and title stay so callers and tests do not change. */
export function AgentAvatar({ identity, size = 28, className }: AgentAvatarProps) {
  return (
    <span
      data-avatar={identity.avatar}
      data-color={identity.color}
      title={identity.displayName}
      aria-label={identity.displayName}
      className={cn("inline-flex shrink-0 items-center justify-center rounded-md border font-semibold select-none",
        identity.classes.text, identity.classes.bg, identity.classes.border, className)}
      style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.5)) }}
    >
      {identity.initial}
    </span>
  );
}

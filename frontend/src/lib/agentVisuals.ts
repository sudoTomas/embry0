/** Per-agent card palette shared by the JobDetailPage AgentCard and the
 * Console board's stage badges, so triage/developer/review read the same
 * everywhere: each role takes the palette colour the pipeline graph draws
 * it in (lib/graph-utils.ts). */
export const AGENT_COLORS: Record<string, { text: string; bg: string; border: string; icon: string }> = {
  triage: { text: "text-purple", bg: "bg-purple-dim", border: "border-purple/25", icon: "\u25C9" },
  developer: { text: "text-amber", bg: "bg-amber/[0.04]", border: "border-amber/25", icon: "\u270E" },
  review: { text: "text-purple", bg: "bg-purple/[0.04]", border: "border-purple/25", icon: "\u2611" },
};

export function getColors(agent: string) {
  return AGENT_COLORS[agent] || { text: "text-white/60", bg: "bg-white/[0.02]", border: "border-white/10", icon: "\u2022" };
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m ${rs}s`;
}

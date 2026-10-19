/**
 * How an agent looks: one answer from its agent_definitions row.
 * Mirrors embry0/agents/identity.py; docs/contracts/identity.json pins both
 * sides (agentIdentity.contract.test.ts). Replaces the hard-coded colour,
 * icon and role maps each surface used to keep.
 */
export const AVATARS = [
  "seer", "smith", "warden", "alchemist", "herald", "arbiter", "scholar", "oracle", "engineer", "rogue", "bard", "golem",
] as const;
export type AvatarId = (typeof AVATARS)[number];

export const PALETTE = ["gold", "cyan", "violet", "rose", "emerald", "amber", "sky", "slate"] as const;
export type PaletteId = (typeof PALETTE)[number];

export const DEFAULT_AVATAR: AvatarId = "golem";
export const DEFAULT_COLOR: PaletteId = "slate";
export const DISPLAY_NAME_MAX = 60;
export const TAGLINE_MAX = 140;

export interface BuiltinIdentity { display_name: string; avatar: string; color: string; tagline: string }

export const BUILTIN_IDENTITY: Record<string, BuiltinIdentity> = {
  analysis: { avatar: "oracle", color: "violet", display_name: "Analysis", tagline: "Finds the pattern." },
  developer: { avatar: "smith", color: "amber", display_name: "Developer", tagline: "Forges the change." },
  onboarding: { avatar: "herald", color: "sky", display_name: "Onboarding", tagline: "Announces a new repository." },
  ops: { avatar: "engineer", color: "amber", display_name: "Ops", tagline: "Keeps it running." },
  "pr-reviewer": { avatar: "arbiter", color: "cyan", display_name: "PR Reviewer", tagline: "Weighs a pull request." },
  qa: { avatar: "alchemist", color: "emerald", display_name: "QA", tagline: "Proves it in the vessel." },
  research: { avatar: "scholar", color: "gold", display_name: "Research", tagline: "Reads everything first." },
  review: { avatar: "warden", color: "rose", display_name: "Reviewer", tagline: "Guards the gate." },
  triage: { avatar: "seer", color: "violet", display_name: "Triage", tagline: "Reads the issue, sets the course." },
};

/** gold is --color-primary; the rest are the Tailwind 400 shades (index.css theme). */
export const PALETTE_HEX: Record<PaletteId, string> = {
  gold: "#d4af37", cyan: "#22d3ee", violet: "#a78bfa", rose: "#fb7185",
  emerald: "#34d399", amber: "#fbbf24", sky: "#38bdf8", slate: "#94a3b8",
};

export interface PaletteClasses { text: string; bg: string; border: string }

export const PALETTE_CLASSES: Record<PaletteId, PaletteClasses> = {
  gold: { text: "text-primary", bg: "bg-primary/[0.06]", border: "border-primary/30" },
  cyan: { text: "text-cyan-400", bg: "bg-cyan-500/[0.04]", border: "border-cyan-500/25" },
  violet: { text: "text-violet-400", bg: "bg-violet-500/[0.04]", border: "border-violet-500/25" },
  rose: { text: "text-rose-400", bg: "bg-rose-500/[0.04]", border: "border-rose-500/25" },
  emerald: { text: "text-emerald-400", bg: "bg-emerald-500/[0.04]", border: "border-emerald-500/25" },
  amber: { text: "text-amber-400", bg: "bg-amber-500/[0.04]", border: "border-amber-500/25" },
  sky: { text: "text-sky-400", bg: "bg-sky-500/[0.04]", border: "border-sky-500/25" },
  slate: { text: "text-slate-400", bg: "bg-slate-500/[0.04]", border: "border-slate-500/25" },
};

export function isAvatarId(v: unknown): v is AvatarId { return typeof v === "string" && (AVATARS as readonly string[]).includes(v); }
export function isPaletteId(v: unknown): v is PaletteId { return typeof v === "string" && (PALETTE as readonly string[]).includes(v); }

/**
 * The agent row a node's type names. Every builtin template spells the
 * review step `reviewer` while the agent row is `review`; the rest name
 * their row directly.
 */
export function resolveAgentType(type: string): string {
  return type === "reviewer" ? "review" : type;
}

/** The fields identityOf reads; a GET /agents row satisfies it, so does a bare type. */
export interface IdentitySource {
  type: string;
  display_name?: string | null;
  avatar?: string | null;
  color?: string | null;
  tagline?: string | null;
}

export interface AgentIdentity {
  type: string;
  displayName: string;
  avatar: AvatarId;
  color: PaletteId;
  tagline: string;
  hex: string;
  classes: PaletteClasses;
  /** First character of the display name, upper-cased; "?" when there is none. */
  initial: string;
}

/** Row value when valid, else the builtin table, else the slug / golem / slate. */
export function identityOf(source: IdentitySource | string): AgentIdentity {
  const row: IdentitySource = typeof source === "string" ? { type: source } : source;
  const builtin = BUILTIN_IDENTITY[row.type];
  const displayName = (row.display_name ?? "").trim() || builtin?.display_name || row.type;
  const avatar = isAvatarId(row.avatar) ? row.avatar : isAvatarId(builtin?.avatar) ? builtin.avatar : DEFAULT_AVATAR;
  const color = isPaletteId(row.color) ? row.color : isPaletteId(builtin?.color) ? builtin.color : DEFAULT_COLOR;
  const tagline = (row.tagline ?? "").trim() || builtin?.tagline || "";
  return {
    type: row.type, displayName, avatar, color, tagline,
    hex: PALETTE_HEX[color], classes: PALETTE_CLASSES[color],
    initial: displayName.trim().charAt(0).toUpperCase() || "?",
  };
}

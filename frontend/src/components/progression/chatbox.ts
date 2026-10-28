import type { RecentEvent } from "@/api/progression";
import { achievementTierName, diaryTierName, fill, rareName, type GamePack } from "@/lib/gamePack";

/** Where a browser keeps the time of the newest event it showed. */
export const SEEN_KEY = "embry0.chatbox.seen";

export function readSeen(): string | null {
  try {
    return window.localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

export function writeSeen(at: string): void {
  try {
    window.localStorage.setItem(SEEN_KEY, at);
  } catch {
    // A browser that keeps nothing shows each message again next time; nothing else depends on it.
  }
}

/** The events newer than ``seen``, oldest first, as the chat box shows them. */
export function freshEvents(recent: readonly RecentEvent[], seen: string): RecentEvent[] {
  return recent.filter((e) => Date.parse(e.at) > Date.parse(seen)).reverse();
}

/**
 * The message, in the content pack's words (#176): a level or a find names the
 * character that earned it. Before the pack answers, there is no message.
 */
export function chatMessage(
  event: RecentEvent,
  pack: GamePack | null,
  skillName: (stat: string) => string,
  characterName: (agentType: string) => string,
): string {
  const messages = pack?.messages;
  if (!messages) return "";
  if (event.kind === "level") {
    return fill(messages.level, { character: characterName(event.agent_type), skill: skillName(event.stat), level: event.level });
  }
  if (event.kind === "agent_level") return fill(messages.agent_level, { character: characterName(event.agent_type), level: event.level });
  if (event.kind === "quest") return fill(messages.quest, { name: event.name });
  if (event.kind === "diary") return fill(messages.diary, { tier: diaryTierName(pack, event.tier), repo: event.repo });
  if (event.kind === "rare") return fill(messages.rare, { character: characterName(event.agent_type), rare: rareName(pack, event.id) });
  if (event.tier === "complete") return fill(messages.achievement_complete, { name: event.name });
  return fill(messages.achievement_tier, { name: event.name, tier: achievementTierName(pack, event.tier) });
}

import type { RecentEvent } from "@/api/progression";
import { TIER_NAME } from "@/lib/tiers";

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

/** The message, in the old game's words. */
export function chatMessage(event: RecentEvent, skillName: (stat: string) => string): string {
  if (event.kind === "level") {
    const skill = skillName(event.stat);
    return `Congratulations, you just advanced a ${skill} level. Your ${skill} level is now ${event.level}.`;
  }
  return `Well done! You have reached ${TIER_NAME[event.tier]} in ${event.name}.`;
}

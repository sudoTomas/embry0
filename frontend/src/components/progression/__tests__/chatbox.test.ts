import { afterEach, describe, expect, it } from "vitest";
import type { RecentEvent } from "@/api/progression";
import { SEEN_KEY, chatMessage, freshEvents, readSeen, writeSeen } from "../chatbox";

const LEVEL: RecentEvent = { kind: "level", at: "2026-10-24T10:00:00+00:00", stat: "smithing", level: 42, agent_type: "developer" };
const UNLOCK: RecentEvent = { kind: "achievement", at: "2026-10-24T11:00:00+00:00", id: "forged_in_fire", name: "Forged in Fire", tier: "steel" };

afterEach(() => window.localStorage.clear());

describe("chat box messages", () => {
  it("says a level and an unlock in the old game's words", () => {
    expect(chatMessage(LEVEL, () => "Smithing")).toBe(
      "Congratulations, you just advanced a Smithing level. Your Smithing level is now 42.",
    );
    expect(chatMessage(UNLOCK, () => "")).toBe("Well done! You have reached Steel in Forged in Fire.");
  });

  it("keeps only what is newer than the last one shown, oldest first", () => {
    const recent = [UNLOCK, LEVEL];
    expect(freshEvents(recent, "2026-10-24T09:00:00Z")).toEqual([LEVEL, UNLOCK]);
    expect(freshEvents(recent, "2026-10-24T10:00:00Z")).toEqual([UNLOCK]);
    expect(freshEvents(recent, "2026-10-24T12:00:00Z")).toEqual([]);
  });

  it("remembers the newest one shown in this browser", () => {
    expect(readSeen()).toBeNull();
    writeSeen(UNLOCK.at);
    expect(window.localStorage.getItem(SEEN_KEY)).toBe(UNLOCK.at);
    expect(readSeen()).toBe(UNLOCK.at);
  });
});

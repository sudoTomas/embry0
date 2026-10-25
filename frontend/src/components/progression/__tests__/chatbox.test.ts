import { afterEach, describe, expect, it } from "vitest";
import type { RecentEvent } from "@/api/progression";
import { SEEN_KEY, chatMessage, freshEvents, readSeen, writeSeen } from "../chatbox";

const LEVEL: RecentEvent = { kind: "level", at: "2026-10-24T10:00:00+00:00", stat: "coding", level: 42, agent_type: "developer" };
const UNLOCK: RecentEvent = { kind: "achievement", at: "2026-10-24T11:00:00+00:00", id: "forged_in_fire", name: "Forged in Fire", tier: "steel" };

afterEach(() => window.localStorage.clear());

describe("chat box messages", () => {
  it("says a character's level and the account's unlock in the old game's words", () => {
    expect(chatMessage(LEVEL, () => "Coding", (type) => (type === "developer" ? "Forge" : type))).toBe(
      "Congratulations, Forge just advanced a Coding level. Its Coding level is now 42.",
    );
    expect(chatMessage(UNLOCK, () => "", () => "")).toBe("Well done! You have reached Steel in Forged in Fire.");
    const objective: RecentEvent = { kind: "achievement", at: "2026-10-24T12:00:00+00:00", id: "long_grind", name: "Long Grind", tier: "complete" };
    expect(chatMessage(objective, () => "", () => "")).toBe("Well done! You have completed the Long Grind achievement.");
    const drop: RecentEvent = { kind: "rare", at: "2026-12-25T10:00:00+00:00", agent_type: "developer", id: "party-hat" };
    expect(chatMessage(drop, () => "", () => "Forge")).toBe("Valuable drop: Forge received a party hat!");
    const up: RecentEvent = { kind: "agent_level", at: "2026-10-24T10:00:00+00:00", agent_type: "developer", level: 96 };
    expect(chatMessage(up, () => "", () => "Forge")).toBe("Congratulations, Forge's level is now 96.");
    const quest: RecentEvent = { kind: "quest", at: "2026-10-24T10:00:00+00:00", id: "gatekeeper", name: "Gatekeeper" };
    expect(chatMessage(quest, () => "", () => "")).toBe("Congratulations! Quest complete: Gatekeeper.");
    const diary: RecentEvent = { kind: "diary", at: "2026-10-24T10:00:00+00:00", repo: "o/app", tier: "medium" };
    expect(chatMessage(diary, () => "", () => "")).toBe("Well done! You have completed the Medium tasks in the o/app diary.");
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

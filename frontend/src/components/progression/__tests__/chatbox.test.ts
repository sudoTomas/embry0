import { afterEach, describe, expect, it } from "vitest";
import type { RecentEvent } from "@/api/progression";
import { SEEN_KEY, chatMessage, freshEvents, readSeen, writeSeen } from "../chatbox";
import { PACK } from "./fixtures";

const LEVEL: RecentEvent = { kind: "level", at: "2026-10-24T10:00:00+00:00", stat: "coding", level: 42, agent_type: "developer" };
const UNLOCK: RecentEvent = { kind: "achievement", at: "2026-10-24T11:00:00+00:00", id: "forged_in_fire", name: "Forged in Fire", tier: "gold" };

afterEach(() => window.localStorage.clear());

describe("chat box messages", () => {
  it("says a character's level and the account's unlock in the old game's words", () => {
    expect(chatMessage(LEVEL, PACK, () => "Coding", (type) => (type === "developer" ? "Forge" : type))).toBe(
      "Forge reached Coding level 42.",
    );
    expect(chatMessage(UNLOCK, PACK, () => "", () => "")).toBe("Forged in Fire reached Gold.");
    const objective: RecentEvent = { kind: "achievement", at: "2026-10-24T12:00:00+00:00", id: "long_grind", name: "Long Grind", tier: "complete" };
    expect(chatMessage(objective, PACK, () => "", () => "")).toBe("Achievement done: Long Grind.");
    const drop: RecentEvent = { kind: "rare", at: "2026-12-25T10:00:00+00:00", agent_type: "developer", id: "paper-crown" };
    expect(chatMessage(drop, PACK, () => "", () => "Forge")).toBe("Forge found a paper crown.");
    const up: RecentEvent = { kind: "agent_level", at: "2026-10-24T10:00:00+00:00", agent_type: "developer", level: 96 };
    expect(chatMessage(up, PACK, () => "", () => "Forge")).toBe("Forge is now level 96.");
    const quest: RecentEvent = { kind: "quest", at: "2026-10-24T10:00:00+00:00", id: "gatekeeper", name: "Gatekeeper" };
    expect(chatMessage(quest, PACK, () => "", () => "")).toBe("Quest done: Gatekeeper.");
    const diary: RecentEvent = { kind: "diary", at: "2026-10-24T10:00:00+00:00", repo: "o/app", tier: "medium" };
    expect(chatMessage(diary, PACK, () => "", () => "")).toBe("The Medium tasks of the o/app diary are done.");
  });

  it("speaks the pack's words, and says nothing before the pack answers", () => {
    const pack = {
      ...PACK,
      messages: { ...PACK.messages!, level: "{character} hit {skill} {level}!", achievement_tier: "{name}: {tier}." },
      tierById: new Map([["gold", { ...PACK.tierById.get("gold")!, name: "Or" }]]),
    };
    expect(chatMessage(LEVEL, pack, () => "Coding", () => "Forge")).toBe("Forge hit Coding 42!");
    expect(chatMessage(UNLOCK, pack, () => "", () => "")).toBe("Forged in Fire: Or.");
    expect(chatMessage(LEVEL, null, () => "Coding", () => "Forge")).toBe("");
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

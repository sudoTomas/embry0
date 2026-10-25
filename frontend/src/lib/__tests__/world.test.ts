import { describe, expect, it } from "vitest";
import type { InboxQuest, Lead, Party, WorldCharacter } from "@/api/world";
import { describeEvent, filterLeads, formatCost, formatSeconds, inboxOrder, levelBand, levelUps, narrate, NO_FILTERS, placeOf, xpDrops } from "../world";
import type { LogEvent } from "@/lib/types";

const party = (over: Partial<Party>): Party => ({
  job_id: "job-1",
  repo: "acme/web",
  area: "Billing",
  title: "Fix the sender",
  status: "running",
  state: "working",
  leader: "developer",
  party: ["developer"],
  working: "developer",
  lead_id: null,
  template: null,
  since: null,
  finished_at: null,
  pr_url: null,
  delivery_state: null,
  stuck: false,
  failure: null,
  ...over,
});

const name = (agent: string) => (agent === "developer" ? "Smith" : agent);

describe("narration (the chat box)", () => {
  it("says nothing on the first read, then each change once", () => {
    expect(narrate(null, [party({})], name)).toEqual([]);
    expect(narrate([], [party({ state: "travelling" })], name)[0].text).toBe("Smith sets off for “Fix the sender” in web · Billing.");
    const before = [party({ state: "working" })];
    expect(narrate(before, [party({ state: "working" })], name)).toEqual([]);
    expect(narrate(before, [party({ state: "waiting" })], name)[0]).toMatchObject({ tone: "alert", text: "Smith needs your answer on “Fix the sender”." });
    expect(narrate(before, [party({ state: "returning", pr_url: "https://x/pull/1" })], name)[0]).toMatchObject({ tone: "win" });
    expect(narrate(before, [party({ state: "defeated", failure: "it ran out of turns" })], name)[0].text).toBe(
      "Oh dear, Smith was defeated on “Fix the sender”: it ran out of turns",
    );
    expect(narrate(before, [party({ stuck: true })], name)[0].text).toBe("Smith has gone quiet on “Fix the sender”.");
  });
});

describe("experience drops", () => {
  const c = (xp: number) => ({ agent_type: "developer", xp }) as WorldCharacter;
  it("float the difference, and nothing on the first read", () => {
    expect(xpDrops(null, [c(10)]).size).toBe(0);
    expect(xpDrops([c(10)], [c(250)]).get("developer")).toBe(240);
    expect(xpDrops([c(10)], [c(10)]).size).toBe(0);
  });
  it("and a level-up names the level reached", () => {
    const at = (level: number) => ({ agent_type: "developer", level }) as WorldCharacter;
    expect(levelUps(null, [at(5)]).size).toBe(0);
    expect(levelUps([at(5)], [at(7)]).get("developer")).toBe(7);
    expect(levelUps([at(5)], [at(5)]).size).toBe(0);
  });
});

describe("the quest board's filters and colours", () => {
  const lead = (over: Partial<Lead>) => ({ id: "x", kind: "case", repo: "acme/web", area: null, level: 30, ...over }) as Lead;
  it("narrows by place, kind and level", () => {
    const leads = [lead({ id: "a" }), lead({ id: "b", repo: "acme/api" }), lead({ id: "c", kind: "issue", level: 80 })];
    expect(filterLeads(leads, NO_FILTERS).map((l) => l.id)).toEqual(["a", "b", "c"]);
    expect(filterLeads(leads, { ...NO_FILTERS, repo: "acme/api" }).map((l) => l.id)).toEqual(["b"]);
    expect(filterLeads(leads, { ...NO_FILTERS, maxLevel: 40 }).map((l) => l.id)).toEqual(["a", "b"]);
    expect(filterLeads(leads, { ...NO_FILTERS, kind: "issue" }).map((l) => l.id)).toEqual(["c"]);
  });
  it("colours a level against yours", () => {
    expect([levelBand(10, 40), levelBand(45, 40), levelBand(70, 40)]).toEqual(["low", "even", "high"]);
  });
});

describe("the inbox and the words", () => {
  it("puts the losses and the open pull requests first", () => {
    const q = (job_id: string, outcome: InboxQuest["outcome"], finished_at: string) => ({ job_id, outcome, finished_at }) as InboxQuest;
    const order = inboxOrder([q("done", "done", "2026-10-25T10:00:00Z"), q("pr", "pr_open", "2026-10-25T09:00:00Z"), q("lost", "failed", "2026-10-24T09:00:00Z")]);
    expect(order.map((x) => x.job_id)).toEqual(["lost", "pr", "done"]);
  });
  it("formats time, cost and place", () => {
    expect([formatSeconds(42), formatSeconds(600), formatSeconds(4_500), formatSeconds(null)]).toEqual(["42s", "10m", "1h 15m", "—"]);
    expect([formatCost(0.001), formatCost(2.5)]).toEqual(["<$0.01", "$2.50"]);
    expect([placeOf("acme/web", "Billing"), placeOf(null)]).toEqual(["web · Billing", "the wilds"]);
  });
});

describe("watching a party", () => {
  const ev = (e: Record<string, unknown>) => e as unknown as LogEvent;
  it("turns its events into short lines and drops the noise", () => {
    expect(describeEvent(ev({ type: "agent_started", agent: "developer" }))).toBe("developer sets to work");
    expect(describeEvent(ev({ type: "tool_call", tool_name: "Edit", summary: "src/app.ts" }))).toBe("Edit src/app.ts");
    expect(describeEvent(ev({ type: "progress", message: "running   the tests" }))).toBe("running the tests");
    expect(describeEvent(ev({ type: "turn_usage", input_tokens: 9 }))).toBeNull();
    expect(describeEvent(ev({ type: "text", text: "x".repeat(300) }))?.endsWith("…")).toBe(true);
  });
});


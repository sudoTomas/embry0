import { describe, expect, it } from "vitest";
import type { MapNode, WorkView } from "@/api/maps";
import { categoryColor, fit, hit, nodeColor, placed, search, toScreen, toWorld, unsentBloat, workState, zoomAt } from "../mapView";

const node = (id: string, over: Partial<MapNode> = {}): MapNode => ({
  id, kind: "script", area: null, label: id, sub: null, path: null, line: null, url: null, repo: null,
  x: 0, y: 0, r: 5, signals: [], ...over,
});
const work = (over: Partial<WorkView>): WorkView => ({
  kind: "remove", repo: "acme/shop", lead_id: null, job_id: "job-1", job_url: null, status: "running", pr_url: null,
  cost_usd: null, error: null, by: "ann", note: "", sent_at: null, finished_at: null, ...over,
});
const colors = { muted: "m", success: "s", warning: "w", danger: "d", info: "i", primary: "p", edge: "e", text: "t" };

describe("the map's view (knowledge maps, spec 2026-10-27)", () => {
  it("fits every part inside the canvas and maps screen and world both ways", () => {
    const pts = placed([node("a", { x: -100, y: 0 }), node("b", { x: 100, y: 50 })]);
    const v = fit(pts, 800, 400);
    const [sx, sy] = toScreen(v, 800, 400, -100, 0);
    expect(sx).toBeGreaterThan(0);
    expect(sx).toBeLessThan(800);
    expect(toWorld(v, 800, 400, sx, sy).map(Math.round)).toEqual([-100, 0]);
  });

  it("places a part that came without a position instead of dropping it", () => {
    const pts = placed([node("a", { x: null, y: null }), node("b", { x: null, y: null })]);
    expect(pts).toHaveLength(2);
    expect(pts[0].x).not.toBe(pts[1].x);
  });

  it("zooms about the cursor and finds the smallest part under it", () => {
    const pts = placed([node("big", { x: 0, y: 0, r: 50 }), node("small", { x: 10, y: 0, r: 4 })]);
    const v = { cx: 0, cy: 0, scale: 1 };
    const z = zoomAt(v, 800, 600, 410, 300, 2);
    expect(toWorld(z, 800, 600, 410, 300).map((n) => Math.round(n))).toEqual([10, 0]);
    expect(hit(pts, v, 800, 600, 410, 300)).toBe("small");
    expect(hit(pts, v, 800, 600, 400 - 40, 300)).toBe("big");
    expect(hit(pts, v, 800, 600, 10, 10)).toBeNull();
  });

  it("reads a part's work as running, a pull request, merged or failed", () => {
    expect(workState(undefined)).toBe("none");
    expect(workState(work({}))).toBe("running");
    expect(workState(work({ pr_url: "https://pr/1", status: "completed" }))).toBe("pr");
    expect(workState(work({ status: "pr_merged", pr_url: "https://pr/1" }))).toBe("merged");
    expect(workState(work({ job_id: null, error: "budget spent", status: null }))).toBe("failed");
    expect(workState(work({ status: "completed" }))).toBe("done");
  });

  it("colours by bloat band, by the viewer's own mark, and by work", () => {
    const ctx = { kinds: [], areas: [], marks: { a: { bloat: 0, keep: 2, unsure: 0, mine: "bloat" as const } }, work: { a: work({ status: "pr_merged" }) } };
    expect(nodeColor(node("a", { bloat: { score: 80, band: "bloat" } }), "bloat", colors, ctx)).toBe("d");
    expect(nodeColor(node("a"), "bloat", colors, ctx)).toBe("m");
    expect(nodeColor(node("a"), "marks", colors, ctx)).toBe("d");
    expect(nodeColor(node("b"), "marks", colors, ctx)).toBe("e");
    expect(nodeColor(node("a"), "work", colors, ctx)).toBe("s");
    expect(categoryColor("script", ["table", "script"])).toBe(categoryColor("script", ["table", "script"]));
    expect(categoryColor("table", ["table", "script"])).not.toBe(categoryColor("script", ["table", "script"]));
  });

  it("lists the parts the viewer marked bloat and never sent, or whose removal failed", () => {
    const marks = {
      a: { bloat: 1, keep: 0, unsure: 0, mine: "bloat" as const },
      b: { bloat: 1, keep: 0, unsure: 0, mine: "bloat" as const },
      c: { bloat: 1, keep: 0, unsure: 0, mine: "bloat" as const },
      d: { bloat: 1, keep: 0, unsure: 0, mine: null },
      e: { bloat: 1, keep: 0, unsure: 0, mine: "bloat" as const },
    };
    const sent = { b: work({}), c: work({ status: "failed" }), e: work({ kind: "analysis" }) };
    expect(unsentBloat(marks, sent)).toEqual(["a", "c", "e"]);
  });

  it("finds parts by every word of the query in the label, id or path", () => {
    const nodes = [node("script:probe.mjs", { label: "probe.mjs", path: "scripts/probe.mjs" }), node("table:orders", { label: "orders" })];
    expect([...search(nodes, "scripts probe")]).toEqual(["script:probe.mjs"]);
    expect(search(nodes, "  ").size).toBe(0);
  });
});

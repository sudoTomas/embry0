import type { MapNode, MarkCounts, WorkView } from "@/api/maps";
import { PALETTE, PALETTE_HEX } from "@/lib/agentIdentity";

/** Knowledge maps (spec 2026-10-27): what the map canvas computes, kept pure so it is tested without a browser. */

export type ColorBy = "kind" | "area" | "bloat" | "marks" | "work";

export interface View {
  /** World coordinate at the canvas centre, and pixels per world unit. */
  cx: number;
  cy: number;
  scale: number;
}

/** The scanner's own layout, or a spiral for parts that came without one. */
export function placed(nodes: readonly MapNode[]): { id: string; x: number; y: number; r: number }[] {
  return nodes.map((n, i) => {
    if (n.x != null && n.y != null) return { id: n.id, x: n.x, y: n.y, r: Math.max(2, n.r ?? 5) };
    const a = i * 2.399963;
    const d = 12 * Math.sqrt(i + 1);
    return { id: n.id, x: Math.cos(a) * d, y: Math.sin(a) * d, r: Math.max(2, n.r ?? 5) };
  });
}

/** The view that shows every point inside a ``width`` by ``height`` canvas with a margin. */
export function fit(points: readonly { x: number; y: number; r: number }[], width: number, height: number): View {
  if (!points.length || width <= 0 || height <= 0) return { cx: 0, cy: 0, scale: 1 };
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of points) {
    minX = Math.min(minX, p.x - p.r);
    maxX = Math.max(maxX, p.x + p.r);
    minY = Math.min(minY, p.y - p.r);
    maxY = Math.max(maxY, p.y + p.r);
  }
  const w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
  const scale = Math.min((width * 0.92) / w, (height * 0.92) / h);
  return { cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, scale };
}

export function toScreen(v: View, width: number, height: number, x: number, y: number): [number, number] {
  return [(x - v.cx) * v.scale + width / 2, (y - v.cy) * v.scale + height / 2];
}

export function toWorld(v: View, width: number, height: number, sx: number, sy: number): [number, number] {
  return [(sx - width / 2) / v.scale + v.cx, (sy - height / 2) / v.scale + v.cy];
}

/** Zoom by ``factor`` keeping the world point under the cursor where it is. */
export function zoomAt(v: View, width: number, height: number, sx: number, sy: number, factor: number): View {
  const [wx, wy] = toWorld(v, width, height, sx, sy);
  const scale = Math.min(200, Math.max(0.005, v.scale * factor));
  return { scale, cx: wx - (sx - width / 2) / scale, cy: wy - (sy - height / 2) / scale };
}

/** The part drawn under a screen point (the smallest one when several overlap), or null. */
export function hit(
  points: readonly { id: string; x: number; y: number; r: number }[],
  v: View,
  width: number,
  height: number,
  sx: number,
  sy: number,
): string | null {
  const [wx, wy] = toWorld(v, width, height, sx, sy);
  let best: string | null = null;
  let bestR = Infinity;
  for (const p of points) {
    const reach = Math.max(p.r, 4 / v.scale);
    if ((p.x - wx) ** 2 + (p.y - wy) ** 2 <= reach * reach && p.r < bestR) {
      best = p.id;
      bestR = p.r;
    }
  }
  return best;
}

/** The colours the canvas draws with: the theme's tokens, read once per draw, with a fallback for tests. */
export interface Colors {
  muted: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
  primary: string;
  edge: string;
  text: string;
}

export function themeColors(el: Element | null): Colors {
  const css = el ? getComputedStyle(el) : null;
  const read = (name: string, fallback: string) => css?.getPropertyValue(name).trim() || fallback;
  return {
    muted: read("--color-muted-foreground", "#a1a1aa"),
    success: read("--color-success", "#22c55e"),
    warning: read("--color-warning", "#f59e0b"),
    danger: read("--color-destructive", "#ef4444"),
    info: read("--color-blue", "#3b82f6"),
    primary: read("--color-primary", "#d4af37"),
    edge: read("--color-border-strong", "#3a4a4a"),
    text: read("--color-foreground", "#fafafa"),
  };
}

/** A stable palette colour for a category (a kind or an area). */
export function categoryColor(key: string | null | undefined, order: readonly string[]): string {
  if (!key) return PALETTE_HEX.slate;
  const i = order.indexOf(key);
  const at = i >= 0 ? i : [...key].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0);
  return PALETTE_HEX[PALETTE[at % PALETTE.length]];
}

/** What a part's work reads as: running, a pull request, merged, failed. */
export function workState(w: WorkView | undefined | null): "none" | "running" | "pr" | "merged" | "done" | "failed" {
  if (!w) return "none";
  const s = w.status ?? "";
  if (s === "pr_merged") return "merged";
  if (w.error && !w.job_id) return "failed";
  if (["failed", "cancelled", "expired", "pr_closed"].includes(s)) return "failed";
  if (w.pr_url) return "pr";
  if (["completed", "partial", "skipped"].includes(s)) return "done";
  return "running";
}

export const WORK_WORDS: Record<ReturnType<typeof workState>, string> = {
  none: "not sent",
  running: "running",
  pr: "pull request open",
  merged: "merged",
  done: "finished",
  failed: "failed",
};

export function nodeColor(
  n: MapNode,
  by: ColorBy,
  c: Colors,
  ctx: { kinds: readonly string[]; areas: readonly string[]; marks?: Record<string, MarkCounts>; work?: Record<string, WorkView> },
): string {
  switch (by) {
    case "kind":
      return categoryColor(n.kind, ctx.kinds);
    case "area":
      return categoryColor(n.area, ctx.areas);
    case "bloat":
      return !n.bloat ? c.muted : n.bloat.band === "bloat" ? c.danger : n.bloat.band === "look" ? c.warning : c.success;
    case "marks": {
      const m = ctx.marks?.[n.id];
      if (!m) return c.edge;
      if (m.mine === "bloat" || (!m.mine && m.bloat >= Math.max(m.keep, m.unsure))) return c.danger;
      if (m.mine === "keep" || m.keep > m.unsure) return c.success;
      return c.warning;
    }
    case "work": {
      const s = workState(ctx.work?.[n.id]);
      return s === "none" ? c.edge : s === "failed" ? c.danger : s === "merged" ? c.success : s === "pr" ? c.primary : c.info;
    }
  }
}

/** The parts the viewer marked bloat that were never sent for removal, or whose last removal failed. */
export function unsentBloat(marks: Record<string, MarkCounts> | undefined, work: Record<string, WorkView> | undefined): string[] {
  return Object.entries(marks ?? {})
    .filter(([, m]) => m.mine === "bloat")
    .filter(([id]) => {
      const w = work?.[id];
      return !w || w.kind !== "remove" || workState(w) === "failed";
    })
    .map(([id]) => id)
    .sort();
}

/** Parts whose label, id or path contains every word of ``query``. */
export function search(nodes: readonly MapNode[], query: string): Set<string> {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return new Set();
  return new Set(
    nodes
      .filter((n) => {
        const text = `${n.label} ${n.id} ${n.path ?? ""}`.toLowerCase();
        return words.every((w) => text.includes(w));
      })
      .map((n) => n.id),
  );
}

/** The repository a part lives in: its own, else the map's when it is a file of it or says how it is removed. */
export function partRepo(n: MapNode, mapRepo: string | null): string | null {
  if (n.repo) return n.repo;
  return mapRepo && (n.path || n.removal) ? mapRepo : null;
}

/** Whether an agent can remove the part with a code change: a file in a repository, or one the scanner says how. */
export function canRemove(n: MapNode, mapRepo: string | null): boolean {
  return !!partRepo(n, mapRepo) && !!(n.path || n.removal);
}

/** Why a part cannot go for ``kind``, or null when it can. */
export function cannotSend(n: MapNode, mapRepo: string | null, kind: "remove" | "fix" | "analysis"): string | null {
  if (!partRepo(n, mapRepo)) return "in no repository";
  if (kind === "remove" && !canRemove(n, mapRepo)) return "a whole system or repository: not a code change";
  return null;
}

import { useEffect, useMemo, useRef, useState } from "react";
import type { MapGraph, MarkCounts, WorkView } from "@/api/maps";
import { type ColorBy, fit, hit, nodeColor, placed, themeColors, toScreen, type View, zoomAt } from "@/lib/mapView";

interface MapCanvasProps {
  graph: MapGraph;
  colorBy: ColorBy;
  marks?: Record<string, MarkCounts>;
  work?: Record<string, WorkView>;
  selected: string | null;
  /** Parts a search or a filter picked out; the rest are dimmed. Empty: nothing is dimmed. */
  highlight: ReadonlySet<string>;
  onSelect: (id: string | null) => void;
}

/**
 * Knowledge maps (spec 2026-10-27): the parts on the scanner's own layout, drawn on a canvas so a map of
 * thousands of parts stays smooth. Drag to pan, wheel or +/- to zoom, 0 to fit, click a part to open it.
 * Areas are labelled rings, a selected part's links are drawn bright. Nothing moves by itself.
 */
export function MapCanvas({ graph, colorBy, marks, work, selected, highlight, onSelect }: MapCanvasProps) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState<View | null>(null);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const drag = useRef<{ x: number; y: number; view: View; moved: boolean } | null>(null);

  const points = useMemo(() => placed(graph.nodes), [graph.nodes]);
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const at = useMemo(() => new Map(points.map((p) => [p.id, p])), [points]);
  const kinds = useMemo(() => graph.kinds.map((k) => k.id), [graph.kinds]);
  const areas = useMemo(() => graph.areas.map((a) => a.id), [graph.areas]);
  const links = useMemo(() => {
    if (!selected) return new Set<string>();
    const out = new Set<string>([selected]);
    for (const e of graph.edges) {
      if (e.s === selected) out.add(e.t);
      if (e.t === selected) out.add(e.s);
    }
    return out;
  }, [graph.edges, selected]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const measure = () => setSize({ w: Math.max(200, el.clientWidth), h: Math.max(200, el.clientHeight) });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const current = view ?? fit(points, size.w, size.h);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext?.("2d");
    if (!el || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    el.width = size.w * dpr;
    el.height = size.h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const c = themeColors(wrap.current);
    const v = current;
    const dim = highlight.size > 0;

    ctx.font = "11px D-DIN, Arial, sans-serif";
    ctx.textAlign = "center";
    for (const a of graph.areas) {
      if (a.x == null || a.y == null || !a.r) continue;
      const [x, y] = toScreen(v, size.w, size.h, a.x, a.y);
      const r = a.r * v.scale;
      if (r < 6) continue;
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = c.edge;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 0.8;
      ctx.fillStyle = c.muted;
      ctx.fillText(a.label, x, y - r - 4);
    }

    ctx.lineWidth = 1;
    for (const e of graph.edges) {
      const s = at.get(e.s), t = at.get(e.t);
      if (!s || !t) continue;
      const lit = selected !== null && (e.s === selected || e.t === selected);
      if (!lit && v.scale < 0.15) continue;
      const [x1, y1] = toScreen(v, size.w, size.h, s.x, s.y);
      const [x2, y2] = toScreen(v, size.w, size.h, t.x, t.y);
      ctx.globalAlpha = lit ? 0.9 : selected || dim ? 0.05 : 0.12;
      ctx.strokeStyle = lit ? c.primary : c.edge;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    }

    const ctxColor = { kinds, areas, marks, work };
    for (const p of points) {
      const n = byId.get(p.id);
      if (!n) continue;
      const [x, y] = toScreen(v, size.w, size.h, p.x, p.y);
      const r = Math.max(1.5, p.r * v.scale);
      if (x < -r || y < -r || x > size.w + r || y > size.h + r) continue;
      const faded = (dim && !highlight.has(p.id)) || (selected !== null && !links.has(p.id));
      ctx.globalAlpha = faded ? 0.15 : 0.95;
      ctx.fillStyle = nodeColor(n, colorBy, c, ctxColor);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      if (p.id === selected || (dim && highlight.has(p.id))) {
        ctx.globalAlpha = 1;
        ctx.strokeStyle = p.id === selected ? c.text : c.primary;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, r + 3, 0, Math.PI * 2);
        ctx.stroke();
        ctx.lineWidth = 1;
      }
      if (!faded && r >= 7) {
        ctx.globalAlpha = 0.85;
        ctx.fillStyle = c.text;
        ctx.fillText(n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label, x, y + r + 12);
      }
    }
    ctx.globalAlpha = 1;
  }, [graph.areas, graph.edges, points, byId, at, kinds, areas, marks, work, colorBy, selected, links, highlight, current, size]);

  const local = (e: { clientX: number; clientY: number }) => {
    const rect = canvas.current?.getBoundingClientRect();
    return rect ? [e.clientX - rect.left, e.clientY - rect.top] : [0, 0];
  };

  // A native listener: React's wheel handler is passive, so it could not keep the page from scrolling.
  const live = useRef({ current, size });
  useEffect(() => {
    live.current = { current, size };
  });
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const { current: v, size: s } = live.current;
      const rect = el.getBoundingClientRect();
      setView(zoomAt(v, s.w, s.h, e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.18 : 1 / 1.18));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "+" || e.key === "=") setView(zoomAt(current, size.w, size.h, size.w / 2, size.h / 2, 1.3));
    else if (e.key === "-") setView(zoomAt(current, size.w, size.h, size.w / 2, size.h / 2, 1 / 1.3));
    else if (e.key === "0") setView(null);
    else if (e.key === "Escape") onSelect(null);
    else return;
    e.preventDefault();
  };

  return (
    <div ref={wrap} className="relative h-full min-h-[420px] w-full overflow-hidden rounded-md border border-border bg-canvas">
      <canvas
        ref={canvas}
        style={{ width: size.w, height: size.h, display: "block", cursor: hover ? "pointer" : "grab" }}
        tabIndex={0}
        role="img"
        aria-label={`Map of ${graph.title}: ${graph.nodes.length} parts. Drag to move, wheel or plus and minus to zoom, 0 to fit.`}
        onKeyDown={onKey}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, view: current, moved: false };
          (e.target as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (d) {
            const dx = e.clientX - d.x, dy = e.clientY - d.y;
            if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
            if (d.moved) setView({ ...d.view, cx: d.view.cx - dx / d.view.scale, cy: d.view.cy - dy / d.view.scale });
            return;
          }
          const [sx, sy] = local(e);
          const id = hit(points, current, size.w, size.h, sx, sy);
          setHover(id ? { id, x: sx, y: sy } : null);
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && !d.moved) {
            const [sx, sy] = local(e);
            onSelect(hit(points, current, size.w, size.h, sx, sy));
          }
        }}
        onPointerLeave={() => setHover(null)}
      />
      {hover && byId.get(hover.id) && (
        <div
          className="pointer-events-none absolute z-10 max-w-xs rounded border border-border bg-popover px-2 py-1 text-xs text-fg shadow"
          style={{ left: Math.min(hover.x + 12, size.w - 220), top: hover.y + 12 }}
        >
          <div className="font-semibold">{byId.get(hover.id)?.label}</div>
          <div className="text-fg-muted">
            {byId.get(hover.id)?.kind}
            {byId.get(hover.id)?.sub ? ` · ${byId.get(hover.id)?.sub}` : ""}
          </div>
        </div>
      )}
      <div className="absolute bottom-2 right-2 flex gap-1">
        <button type="button" className="rounded border border-border bg-surface px-2 text-sm text-fg-secondary hover:text-fg" aria-label="Zoom in" onClick={() => setView(zoomAt(current, size.w, size.h, size.w / 2, size.h / 2, 1.3))}>+</button>
        <button type="button" className="rounded border border-border bg-surface px-2 text-sm text-fg-secondary hover:text-fg" aria-label="Zoom out" onClick={() => setView(zoomAt(current, size.w, size.h, size.w / 2, size.h / 2, 1 / 1.3))}>−</button>
        <button type="button" className="rounded border border-border bg-surface px-2 text-xs text-fg-secondary hover:text-fg" onClick={() => setView(null)}>Fit</button>
      </div>
    </div>
  );
}

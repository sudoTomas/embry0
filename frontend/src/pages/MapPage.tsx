import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";
import type { MapGraph } from "@/api/maps";
import { toastApiError } from "@/api/errorToast";
import { isNotFound } from "@/api/errorDetail";
import { type RunWith, runWithBody } from "@/api/runWith";
import { PageError } from "@/components/PageError";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { PartPanel } from "@/components/maps/PartPanel";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Select } from "@/components/ui/Select";
import { useMap, useMapMarks, useMapWork, useSendParts } from "@/hooks/useMaps";
import { type ColorBy, search, unsentBloat, workState } from "@/lib/mapView";
import { formatDateTime } from "@/lib/utils";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";

const COLOR_BY: { id: ColorBy; label: string }[] = [
  { id: "bloat", label: "Bloat score" },
  { id: "marks", label: "Marks" },
  { id: "work", label: "Agent work" },
  { id: "kind", label: "Kind" },
  { id: "area", label: "Area" },
];

type Show = "all" | "bloat" | "marked" | "sent";

/** "Send my bloat marks": every part the viewer marked bloat and has not sent, removed in one batch. */
function SendMarksDialog({
  slug,
  graph,
  ids,
  by,
  onClose,
}: {
  slug: string;
  graph: MapGraph;
  ids: string[];
  by: string;
  onClose: () => void;
}) {
  const send = useSendParts(slug);
  const [runWith, setRunWith] = useState<RunWith>({});
  const [note, setNote] = useState("");
  const labels = new Map(graph.nodes.map((n) => [n.id, n.label]));
  const repos = [...new Set(ids.map((id) => graph.nodes.find((n) => n.id === id)?.repo ?? graph.repo).filter(Boolean))];
  const go = async () => {
    let sent = 0;
    const failed: string[] = [];
    for (let i = 0; i < ids.length; i += 50) {
      try {
        const r = await send.mutateAsync({ node_ids: ids.slice(i, i + 50), kind: "remove", note, by, ...runWithBody(runWith) });
        sent += r.sent;
        failed.push(...r.results.filter((x) => !x.job_id).map((x) => `${labels.get(x.node_id) ?? x.node_id}: ${x.error}`));
      } catch (e) {
        toastApiError(e as Error, "Not sent");
        return;
      }
    }
    if (failed.length) toast.error(`${sent} sent, ${failed.length} not: ${failed.slice(0, 3).join("; ")}`);
    else toast.success(`${sent} sent for removal`);
    onClose();
  };
  return (
    <Dialog onClose={onClose} title={`Remove ${ids.length} part${ids.length === 1 ? "" : "s"} you marked bloat`} className="max-w-xl">
      <div className="space-y-3 p-4 text-sm">
        <p className="text-fg-secondary">
          Each becomes one job: an agent deletes the part and what only it uses, and opens a pull request. Your marks are
          the decision; the agents do not re-assess them.
        </p>
        <ul className="max-h-48 space-y-0.5 overflow-auto rounded border border-border p-2 text-xs">
          {ids.map((id) => <li key={id}>{labels.get(id) ?? id}</li>)}
        </ul>
        <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="A note for the agents (optional)" aria-label="A note for the agents" />
        <RunWithControl value={runWith} onChange={setRunWith} repo={repos.length === 1 ? (repos[0] as string) : null} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Not now</Button>
          <Button disabled={send.isPending} onClick={() => void go()}>{send.isPending ? "Sending…" : `Send ${ids.length}`}</Button>
        </div>
      </div>
    </Dialog>
  );
}

/** Knowledge maps (spec 2026-10-27): one map — the parts on the scanner's layout, a part's panel, sending work. */
export function MapPage() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const isAdmin = useIsAdmin();
  const map = useMap(slug);
  const marks = useMapMarks(slug, by);
  const work = useMapWork(slug);
  const [colorBy, setColorBy] = useState<ColorBy>("bloat");
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("all");
  const [batch, setBatch] = useState(false);
  const selected = params.get("part");
  const select = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("part", id);
    else next.delete("part");
    setParams(next, { replace: true });
  };

  const highlight = useMemo(() => {
    const nodes = map.data?.nodes ?? [];
    const found = search(nodes, query);
    let picked: Set<string> | null = null;
    if (show === "bloat") picked = new Set(nodes.filter((n) => n.bloat?.band === "bloat").map((n) => n.id));
    if (show === "marked") picked = new Set(Object.keys(marks.data ?? {}));
    if (show === "sent") picked = new Set(Object.keys(work.data ?? {}));
    if (picked && query) return new Set([...found].filter((id) => picked?.has(id)));
    return picked ?? found;
  }, [map.data, query, show, marks.data, work.data]);
  const unsent = useMemo(() => unsentBloat(marks.data, work.data), [marks.data, work.data]);
  const running = Object.values(work.data ?? {}).filter((w) => workState(w) === "running").length;

  if (map.isLoading) return <Skeleton className="h-[70vh] w-full" />;
  if (map.isError || !map.data) {
    return isNotFound(map.error) ? (
      <PageError message="No map with that name" back={{ to: "/maps", label: "All maps" }} />
    ) : (
      <PageError message="The map did not load" onRetry={() => void map.refetch()} />
    );
  }
  const g = map.data;
  return (
    <div className="flex h-[calc(100vh-7rem)] min-h-[560px] flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-fg-muted"><Link className="hover:text-fg" to="/maps">Maps</Link> /</p>
          <h1 className="title-pixel text-xl font-bold">{g.title}</h1>
          <p className="text-xs text-fg-muted">
            {g.repo ?? "organization map"} · {g.nodes.length.toLocaleString()} parts
            {g.generated_at ? ` · scanned ${formatDateTime(g.generated_at)}` : ""}
            {running ? ` · ${running} agent job${running === 1 ? "" : "s"} running` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="w-56" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a part" aria-label="Find a part" />
          <Select className="w-40" value={show} onChange={(e) => setShow(e.target.value as Show)} aria-label="Show">
            <option value="all">All parts</option>
            <option value="bloat">Bloat candidates</option>
            <option value="marked">Marked</option>
            <option value="sent">Sent to agents</option>
          </Select>
          <Select className="w-36" value={colorBy} onChange={(e) => setColorBy(e.target.value as ColorBy)} aria-label="Colour by">
            {COLOR_BY.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </Select>
          <Button disabled={!isAdmin || unsent.length === 0} onClick={() => setBatch(true)} title={isAdmin ? undefined : "Only an admin sends work"}>
            Send my bloat marks ({unsent.length})
          </Button>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
        <div className="min-w-0 flex-1">
          <MapCanvas
            graph={g}
            colorBy={colorBy}
            marks={marks.data}
            work={work.data}
            selected={selected}
            highlight={highlight}
            onSelect={select}
          />
        </div>
        {selected && (
          <aside className="max-h-[50vh] w-full shrink-0 overflow-y-auto rounded-md border border-border bg-surface p-3 lg:max-h-none lg:w-[380px]" aria-label="The selected part">
            <PartPanel slug={slug} nodeId={selected} by={by} canSend={isAdmin} onClose={() => select(null)} />
          </aside>
        )}
      </div>
      {(query || show !== "all") && (
        <p className="text-xs text-fg-muted">{highlight.size.toLocaleString()} parts picked out; the rest are dimmed.</p>
      )}
      {batch && <SendMarksDialog slug={slug} graph={g} ids={unsent} by={by} onClose={() => setBatch(false)} />}
    </div>
  );
}

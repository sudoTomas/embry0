import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { type MapOwner, ownerOf, type SendKind } from "@/api/maps";
import { apiErrorMessage, isConflict, isNotFound } from "@/api/errorDetail";
import { PageError } from "@/components/PageError";
import { BatchSendDialog } from "@/components/maps/BatchSendDialog";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { MapOwnerControl } from "@/components/maps/MapOwnerControl";
import { PartPanel } from "@/components/maps/PartPanel";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Select } from "@/components/ui/Select";
import { useMap, useMapMarks, useMapWork } from "@/hooks/useMaps";
import { type ColorBy, canRemove, ownerWords, search, unsentBloat, workState } from "@/lib/mapView";
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

/**
 * Knowledge maps (spec 2026-10-27): one map — the parts on the scanner's layout, a part's panel, sending work.
 *
 * `?owner=` names whose map of the slug it is (#180; empty: the map of no owner). Without it the server answers with
 * the one map of the slug the viewer sees, so a link from before owners keeps working.
 */
export function MapPage() {
  const { slug = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const owner: MapOwner = params.get("owner") ?? undefined;
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const isAdmin = useIsAdmin();
  const map = useMap(slug, owner);
  const marks = useMapMarks(slug, by, owner);
  const work = useMapWork(slug, owner);
  const [colorBy, setColorBy] = useState<ColorBy>("bloat");
  const [query, setQuery] = useState("");
  const [show, setShow] = useState<Show>("all");
  const [batch, setBatch] = useState<{ ids: string[]; kind: SendKind; title: string } | null>(null);
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
  const unsent = useMemo(() => {
    // Only a file or folder in a repository can be removed; a whole system's mark stays on the map.
    const removable = new Set((map.data?.nodes ?? []).filter((n) => canRemove(n, map.data?.repo ?? null)).map((n) => n.id));
    return unsentBloat(marks.data, work.data).filter((id) => removable.has(id));
  }, [map.data, marks.data, work.data]);
  const running = Object.values(work.data ?? {}).filter((w) => workState(w) === "running").length;

  if (map.isLoading) return <Skeleton className="h-[70vh] w-full" />;
  if (map.isError || !map.data) {
    if (isNotFound(map.error)) return <PageError message="No map with that name" back={{ to: "/maps", label: "All maps" }} />;
    // Several owners have a map of this slug and the address names none: the list links each by its owner.
    if (isConflict(map.error)) return <PageError message={apiErrorMessage(map.error)} back={{ to: "/maps", label: "All maps" }} />;
    return <PageError message="The map did not load" onRetry={() => void map.refetch()} />;
  }
  const g = map.data;
  const whose = ownerWords(g);
  const follow = (to: string) => {
    const next = new URLSearchParams(params);
    next.set("owner", to);
    setParams(next, { replace: true });
  };
  return (
    <div className="flex h-[calc(100vh-7rem)] min-h-[560px] flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-fg-muted"><Link className="hover:text-fg" to="/maps">Maps</Link> /</p>
          <h1 className="title-page text-xl font-bold">{g.title}</h1>
          <p className="text-xs text-fg-muted">
            {whose !== null && <span data-testid="map-owner">{whose} · </span>}
            {g.repo ?? "organization map"} · {g.nodes.length.toLocaleString()} parts
            {g.generated_at ? ` · scanned ${formatDateTime(g.generated_at)}` : ""}
            {running ? ` · ${running} agent job${running === 1 ? "" : "s"} running` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {isAdmin && whose !== null && <MapOwnerControl slug={slug} owner={owner ?? ownerOf(g)} onMoved={follow} />}
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
          <Button
            disabled={!isAdmin || unsent.length === 0}
            onClick={() => setBatch({ ids: unsent, kind: "remove", title: `Remove the ${unsent.length} parts you marked bloat` })}
            title={isAdmin ? undefined : "Only an admin sends work"}
          >
            Send my bloat marks ({unsent.length})
          </Button>
          {highlight.size > 0 && (
            <Button
              variant="outline"
              disabled={!isAdmin}
              onClick={() =>
                setBatch({
                  ids: [...highlight],
                  kind: show === "bloat" || show === "marked" ? "remove" : "analysis",
                  title: `Send ${highlight.size.toLocaleString()} parts picked out`,
                })
              }
              title={isAdmin ? undefined : "Only an admin sends work"}
            >
              Send {highlight.size.toLocaleString()} picked out
            </Button>
          )}
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
            <PartPanel slug={slug} owner={owner} nodeId={selected} by={by} canSend={isAdmin} onClose={() => select(null)} />
          </aside>
        )}
      </div>
      {(query || show !== "all") && (
        <p className="text-xs text-fg-muted">{highlight.size.toLocaleString()} parts picked out; the rest are dimmed.</p>
      )}
      {batch && (
        <BatchSendDialog
          slug={slug}
          owner={owner}
          graph={g}
          ids={batch.ids}
          initialKind={batch.kind}
          title={batch.title}
          by={by}
          onClose={() => setBatch(null)}
        />
      )}
    </div>
  );
}

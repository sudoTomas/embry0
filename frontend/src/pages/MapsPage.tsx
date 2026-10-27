import { Link } from "react-router";
import { Network } from "lucide-react";
import { PageError } from "@/components/PageError";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useMaps } from "@/hooks/useMaps";
import { formatDateTime } from "@/lib/utils";

/** Knowledge maps (spec 2026-10-27): every map a scanner uploaded, one card each. */
export function MapsPage() {
  const maps = useMaps();
  return (
    <div className="space-y-4">
      <div>
        <h1 className="title-pixel text-xl font-bold">Maps</h1>
        <p className="text-sm text-fg-secondary">
          A scanner's map of a codebase or an organization: its parts, how they link, and what each costs to keep. Mark
          a part bloat and an agent can remove it.
        </p>
      </div>
      {maps.isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : maps.isError ? (
        <PageError message="Maps did not load" onRetry={() => void maps.refetch()} />
      ) : !maps.data?.length ? (
        <EmptyState
          icon={Network}
          title="No maps yet"
          description="A scanner uploads one with PUT /api/v1/maps/<slug>; the format is in docs/maps.md."
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {maps.data.map((m) => (
            <Link
              key={m.slug}
              to={`/maps/${encodeURIComponent(m.slug)}`}
              className="block rounded-lg border border-border bg-surface p-4 transition-colors hover:border-border-strong"
            >
              <div className="font-semibold text-fg">{m.title}</div>
              <div className="text-xs text-fg-muted">{m.repo ?? "organization map"}</div>
              <div className="mt-2 text-sm text-fg-secondary">
                {m.nodes.toLocaleString()} parts · {m.edges.toLocaleString()} links
              </div>
              <div className="mt-1 text-xs text-fg-muted">
                {m.uploaded_at ? `Updated ${formatDateTime(m.uploaded_at)}` : ""}
                {m.source ? ` · ${m.source}` : ""}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

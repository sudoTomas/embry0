/**
 * Phase 5D: dashboard view of the run-level affected-set decision.
 *
 * Reads the qa_run_metadata snapshot persisted by qa_orchestrator_node
 * and renders three sections:
 *   - "Apps run (N)"      — the apps that were QA'd
 *   - "Apps skipped (N)"  — declared apps NOT in apps_to_qa
 *   - "Changed files (N)" — the diff that drove the affected-set call
 * plus a header with `force_all_apps` indicator + base_branch.
 *
 * `dep_graph` is currently always empty — when the workspace_provider
 * starts exposing edges, this component will render them as
 * source -> target rows. The list view already works without it for MVP.
 */
import { useAffectedSet } from "@/hooks/useQaDashboard";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";

interface Props {
  runId: string;
}

function ListSection({
  title,
  items,
  emptyHint,
  testid,
}: {
  title: string;
  items: string[];
  emptyHint: string;
  testid: string;
}) {
  return (
    <section className="rounded-md border bg-card p-4" data-testid={testid}>
      <header className="mb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/70">
          {title} ({items.length})
        </h2>
      </header>
      {items.length === 0 ? (
        <p className="text-sm text-white/40">{emptyHint}</p>
      ) : (
        <ul className="space-y-1 text-sm">
          {items.map((item) => (
            <li
              key={item}
              className="break-words rounded-sm bg-white/5 px-2 py-1 font-mono text-white/80"
            >
              {item}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function AffectedSetView({ runId }: Props) {
  const { data, isLoading, isError, error, refetch } = useAffectedSet(runId);

  // 404 from the backend = no metadata row was written for this run.
  // Distinguish from a generic load failure so the user gets a clear hint.
  if (isError) {
    const status = (error as { response?: { status?: number } } | undefined)
      ?.response?.status;
    if (status === 404) {
      return (
        <div className="p-6">
          <EmptyState
            stage="qa"
            title="No affected-set recorded for this run"
            description="The run finished before fan-out resolution (e.g. infra error during init), or it predates the affected-set persistence layer."
          />
        </div>
      );
    }
    return (
      <PageError
        message="Failed to load affected-set"
        onRetry={() => refetch()}
      />
    );
  }
  if (isLoading || !data) return <TableSkeleton />;

  return (
    <div className="space-y-4 p-6" data-testid="affected-set-view">
      <header className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-bold font-mono">{data.job_id}</h1>
        {data.force_all_apps && (
          <span
            data-testid="force-all-apps-badge"
            className="rounded-sm bg-amber-500/20 px-2 py-0.5 text-xs uppercase text-amber-300"
          >
            force-all-apps
          </span>
        )}
        {data.base_branch && (
          <span className="text-sm text-white/50">
            base: <span className="font-mono">{data.base_branch}</span>
          </span>
        )}
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <ListSection
          title="Apps run"
          items={data.apps_to_qa}
          emptyHint="No apps ran for this diff."
          testid="apps-run-section"
        />
        <ListSection
          title="Apps skipped"
          items={data.apps_skipped}
          emptyHint="Every declared app ran."
          testid="apps-skipped-section"
        />
        <ListSection
          title="Changed files"
          items={data.changed_files}
          emptyHint="No files in diff (force_all_apps path or empty diff)."
          testid="changed-files-section"
        />
      </div>

      <section
        className="rounded-md border bg-card p-4"
        data-testid="dep-graph-section"
      >
        <header className="mb-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-white/70">
            Dep graph ({data.dep_graph.length})
          </h2>
        </header>
        {data.dep_graph.length === 0 ? (
          <p className="text-sm text-white/40">
            Dep graph not yet exposed by the workspace provider — list view
            available once the provider surfaces workspace edges.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {data.dep_graph.map((edge, idx) => (
              <li
                key={`${edge.source}->${edge.target}-${idx}`}
                className="break-words rounded-sm bg-white/5 px-2 py-1 font-mono text-white/80"
              >
                {edge.source} <span className="text-white/40">→</span>{" "}
                {edge.target}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

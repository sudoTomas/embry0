import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { useProviderOverrides } from "@/hooks/useQaDashboard";
import { ProvidersAdminForm } from "@/components/qa/ProvidersAdminForm";
import { deleteProviderOverride } from "@/api/qaDashboard";
import type { WorkspaceProviderOverride } from "@/lib/types";

const QUERY_KEY = ["qa-dashboard", "provider-overrides"];

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  const now = Date.now();
  const diffSec = Math.max(0, Math.round((now - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 48) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

function previewConfig(config: Record<string, unknown>): string {
  try {
    const compact = JSON.stringify(config);
    if (compact.length <= 80) return compact;
    return `${compact.slice(0, 77)}...`;
  } catch {
    return "{...}";
  }
}

export function QaProvidersAdminPage() {
  const { data, isLoading, isError, refetch } = useProviderOverrides();
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<
    | { kind: "list" }
    | { kind: "add" }
    | { kind: "edit"; row: WorkspaceProviderOverride }
  >({ kind: "list" });

  if (isError) {
    return (
      <PageError
        message="Failed to load provider overrides"
        onRetry={() => refetch()}
      />
    );
  }
  if (isLoading || !data) return <DashboardSkeleton />;

  async function handleDelete(repo: string) {
    if (!window.confirm(`Delete override for ${repo}?`)) return;
    try {
      await deleteProviderOverride(repo);
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      toast.success(`Override deleted for ${repo}`);
    } catch {
      toast.error(`Failed to delete override for ${repo}`);
    }
  }

  function handleSaved() {
    queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    setMode({ kind: "list" });
    toast.success("Override saved");
  }

  return (
    <div className="space-y-6 p-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Provider overrides</h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Per-repo workspace_provider config that overrides{" "}
            <code className="font-mono">.embry0/qa.yaml</code>. Useful when
            you want to iterate on{" "}
            <code className="font-mono">affected_filter</code> /{" "}
            <code className="font-mono">apps_glob</code> without committing.
          </p>
        </div>
        {mode.kind === "list" && (
          <Button onClick={() => setMode({ kind: "add" })}>
            <Plus className="h-4 w-4" />
            Add override
          </Button>
        )}
      </header>

      {mode.kind === "add" && (
        <ProvidersAdminForm
          onSaved={handleSaved}
          onCancel={() => setMode({ kind: "list" })}
        />
      )}
      {mode.kind === "edit" && (
        <ProvidersAdminForm
          initial={mode.row}
          onSaved={handleSaved}
          onCancel={() => setMode({ kind: "list" })}
        />
      )}

      {data.length === 0 && mode.kind === "list" ? (
        <EmptyState
          stage="qa"
          title="No overrides"
          description="All repos use the workspace_provider config in their .embry0/qa.yaml."
        />
      ) : (
        <div
          className="rounded-lg border border-white/[0.06] divide-y divide-white/[0.06]"
          data-testid="provider-overrides-list"
        >
          {data.map((row) => (
            <div
              key={row.repo}
              data-testid={`provider-override-row-${row.repo}`}
              className="flex items-center justify-between gap-4 p-4"
            >
              <div className="min-w-0 flex-1">
                <div className="font-mono text-sm font-semibold truncate">
                  {row.repo}
                </div>
                <div className="text-xs text-muted-foreground mt-1">
                  <span className="font-mono">{row.provider_type}</span>
                  {" · "}
                  <span title={JSON.stringify(row.config)} className="font-mono">
                    {previewConfig(row.config)}
                  </span>
                  {" · "}
                  <span>updated {formatRelative(row.updated_at)}</span>
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setMode({ kind: "edit", row })}
                  aria-label={`Edit ${row.repo}`}
                >
                  <Pencil className="h-3.5 w-3.5" />
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleDelete(row.repo)}
                  aria-label={`Delete ${row.repo}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { ReactFlow, Background, Controls, type NodeTypes } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { toast } from "sonner";
import {
  deadLetterTask,
  deployTask,
  fetchTasks,
  fetchTaskBlockedBy,
  requeueTask,
  retryTask,
  stopTask,
  type AgentTask,
  type AgentTaskStatus,
} from "@/api/agent";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { BlockedNode } from "@/components/tasks/BlockedNode";
import { buildBlockedByGraph } from "@/lib/blockedByGraph";

// Stable identity for ReactFlow — re-rendering with a new nodeTypes object
// triggers an internal warning.
const NODE_TYPES: NodeTypes = { blockedNode: BlockedNode };

const TASKS_KEY: QueryKey = ["agent", "tasks"];
const blockedByKey = (id: string): QueryKey => [
  "agent",
  "tasks",
  id,
  "blocked-by",
];

type ActionFn = (id: string) => Promise<AgentTask>;

const ROW_ACTIONS: ReadonlyArray<{
  key: "deploy" | "requeue" | "retry" | "stop" | "dead-letter";
  fn: ActionFn;
  label: string;
}> = [
  { key: "deploy", fn: deployTask, label: "Deploy" },
  { key: "requeue", fn: requeueTask, label: "Requeue" },
  { key: "retry", fn: retryTask, label: "Retry" },
  { key: "stop", fn: stopTask, label: "Stop" },
  { key: "dead-letter", fn: deadLetterTask, label: "Dead-letter" },
];

const STATUS_TONE: Record<AgentTaskStatus, string> = {
  running: "text-cyan-400 border-cyan-500/40",
  queued: "text-muted-foreground border-white/10",
  done: "text-emerald-400 border-emerald-500/40",
  failed: "text-red-400 border-red-500/40",
  dead_letter: "text-fuchsia-400 border-fuchsia-500/40",
};

export function TasksPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const tasksQ = useQuery({
    queryKey: TASKS_KEY,
    queryFn: fetchTasks,
    refetchInterval: 30_000,
  });

  const blockedByQ = useQuery({
    queryKey: selectedId ? blockedByKey(selectedId) : ["agent", "tasks", "__none__", "blocked-by"],
    queryFn: () => fetchTaskBlockedBy(selectedId!),
    enabled: Boolean(selectedId),
  });

  const action = useMutation({
    mutationFn: ({ id, fn }: { id: string; fn: ActionFn }) => fn(id),
    onSuccess: (_data, { id }) => {
      // The list cache is always stale after a successful action. The
      // selected task's blocked-by graph also depends on status of its
      // siblings (status badges + edges may shift), so invalidate it too
      // — even when `id` isn't the selected row.
      queryClient.invalidateQueries({ queryKey: TASKS_KEY });
      if (selectedId) {
        queryClient.invalidateQueries({ queryKey: blockedByKey(selectedId) });
      }
      toast.success(`Task ${id} updated`);
    },
    onError: (_e, { id }) => {
      toast.error(`Action failed for ${id}`);
    },
  });

  if (tasksQ.isError) {
    return (
      <PageError message="Failed to load tasks" onRetry={() => tasksQ.refetch()} />
    );
  }
  if (tasksQ.isLoading || !tasksQ.data) return <DashboardSkeleton />;
  const tasks = tasksQ.data;

  if (tasks.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-bold">Tasks</h1>
        <EmptyState title="No tasks" description="The agent has not produced any tasks yet." />
      </div>
    );
  }

  const graph =
    selectedId && blockedByQ.data
      ? buildBlockedByGraph(
          blockedByQ.data,
          tasks.find((t) => t.id === selectedId)?.title ?? selectedId,
        )
      : null;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Tasks</h1>
        <span className="text-xs text-muted-foreground">{tasks.length} total</span>
      </header>

      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/[0.02] text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Task</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="text-right px-3 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr
                key={t.id}
                data-testid={`task-row-${t.id}`}
                data-selected={selectedId === t.id ? "true" : "false"}
                className="cursor-pointer border-t border-border hover:bg-white/[0.02] data-[selected=true]:bg-white/[0.04]"
                onClick={() => setSelectedId(t.id)}
              >
                <td className="px-3 py-2">
                  <div className="font-medium truncate">{t.title ?? t.id}</div>
                  <div className="text-[10px] font-mono text-muted-foreground">{t.id}</div>
                </td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-block rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wider ${STATUS_TONE[t.status]}`}
                  >
                    {t.status.replace("_", " ")}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="inline-flex flex-wrap gap-1 justify-end">
                    {ROW_ACTIONS.map((a) => (
                      <Button
                        key={a.key}
                        size="sm"
                        variant="outline"
                        aria-label={`${a.key} ${t.id}`}
                        disabled={action.isPending}
                        onClick={(e) => {
                          e.stopPropagation();
                          action.mutate({ id: t.id, fn: a.fn });
                        }}
                      >
                        {a.label}
                      </Button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedId && (
        <section
          data-testid="blocked-by-graph"
          className="rounded-lg border border-border bg-card p-4"
        >
          <header className="mb-3 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold">Dependency graph</h2>
            <span className="text-xs text-muted-foreground">
              {blockedByQ.isLoading ? "loading…" : `${graph?.nodes.length ?? 0} nodes`}
            </span>
          </header>
          <div style={{ height: 280 }}>
            {graph && (
              <ReactFlow
                nodes={graph.nodes}
                edges={graph.edges}
                nodeTypes={NODE_TYPES}
                fitView
                proOptions={{ hideAttribution: true }}
                panOnDrag
                zoomOnScroll={false}
              >
                <Background gap={20} color="rgba(255,255,255,0.04)" />
                <Controls showInteractive={false} />
              </ReactFlow>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

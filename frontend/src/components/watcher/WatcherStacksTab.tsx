import { useState } from "react";
import { RadioTower } from "lucide-react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { useDeleteWatcherStack, useSaveWatcherStack, useWatcherStacks } from "@/hooks/useWatcherStacks";
import { AdminButton } from "@/components/auth/AdminButton";
import { StackEditorDialog } from "@/components/watcher/StackEditorDialog";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Menu } from "@/components/ui/Menu";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { providerLabel } from "@/lib/status";
import { useIsAdmin } from "@/stores/authStore";
import type { WatcherStack, WatcherStackWrite } from "@/lib/types";

type Open = { kind: "add" } | { kind: "edit"; stack: WatcherStack } | { kind: "delete"; stack: WatcherStack } | null;

/** The PUT body of a stored stack: everything but its name. */
function stackBody(stack: WatcherStack, enabled: boolean): WatcherStackWrite {
  return { logql: stack.logql, tracker: stack.tracker, tracker_target: stack.tracker_target, settings: stack.settings, enabled };
}

/**
 * The stacks embry0 watches (spec 3.7): readable by every tier, like the
 * route; adding, editing, enabling, disabling and deleting are an admin's.
 * Deleting stops the watching and keeps the record of what it found.
 */
export function WatcherStacksTab() {
  const [open, setOpen] = useState<Open>(null);
  const isAdmin = useIsAdmin();
  const list = useWatcherStacks();
  const save = useSaveWatcherStack();
  const del = useDeleteWatcherStack();

  const toggle = (stack: WatcherStack) =>
    save.mutate(
      { repo: stack.repo, body: stackBody(stack, !stack.enabled) },
      {
        onSuccess: () => toast.success(`${stack.enabled ? "Disabled" : "Enabled"} ${stack.repo}`),
        onError: (e) => toastApiError(e, `Could not ${stack.enabled ? "disable" : "enable"} ${stack.repo}`),
      },
    );

  const remove = (stack: WatcherStack) =>
    del.mutate(stack.repo, {
      onSuccess: () => {
        toast.success(`Stopped watching ${stack.repo}`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, `Could not stop watching ${stack.repo}`);
      },
    });

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load watched stacks" onRetry={() => void list.refetch()} />;
  } else if (list.isPending || !list.data) {
    body = <TableSkeleton />;
  } else if (list.data.stacks.length === 0) {
    body = (
      <EmptyState icon={RadioTower} title="No watched stacks" description="Add a stack to point the watcher at a deployment's logs. Nothing is analysed until a stack is enabled." />
    );
  } else {
    body = (
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col" className="hidden sm:table-cell">LogQL</th>
              <th scope="col">Tracker</th>
              <th scope="col">State</th>
              <th scope="col" className="hidden sm:table-cell">Settings</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.stacks.map((stack) => {
              const overrides = Object.keys(stack.settings).length;
              return (
                <tr key={stack.repo} data-testid="watcher-stack-row">
                  <td className="whitespace-nowrap font-mono text-xs">{stack.repo}</td>
                  <td className="hidden w-full max-w-0 truncate font-mono text-xs text-fg-muted sm:table-cell" title={stack.logql}>{stack.logql}</td>
                  <td className="whitespace-nowrap text-fg-secondary">{providerLabel(stack.tracker)}</td>
                  <td className="whitespace-nowrap">
                    <Badge tone={stack.enabled ? "success" : "neutral"}>{stack.enabled ? "Enabled" : "Disabled"}</Badge>
                  </td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">
                    {overrides === 0 ? "Defaults" : `${overrides} ${overrides === 1 ? "override" : "overrides"}`}
                  </td>
                  <td className="w-px">
                    <Menu
                      label={`Actions for ${stack.repo}`}
                      items={[
                        { label: "Edit", onSelect: () => setOpen({ kind: "edit", stack }), disabled: !isAdmin },
                        { label: stack.enabled ? "Disable" : "Enable", onSelect: () => toggle(stack), disabled: !isAdmin || save.isPending },
                        { label: "Delete", onSelect: () => setOpen({ kind: "delete", stack }), destructive: true, disabled: !isAdmin },
                      ]}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          What the watcher reads and where it files what it finds. Adding a stack points embry0&apos;s log analysis, and the spend behind it, at another deployment.
        </p>
        <AdminButton type="button" variant="outline" size="sm" onClick={() => setOpen({ kind: "add" })}>
          Add stack
        </AdminButton>
      </div>
      {body}
      {(open?.kind === "add" || open?.kind === "edit") && (
        <StackEditorDialog
          stack={open.kind === "edit" ? open.stack : undefined}
          existing={list.data?.stacks.map((s) => s.repo) ?? []}
          overridable={list.data?.overridable_settings ?? []}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "delete" && (
        <ConfirmDialog
          title={`Stop watching ${open.stack.repo}?`}
          confirmLabel="Delete"
          destructive
          pending={del.isPending}
          onConfirm={() => remove(open.stack)}
          onClose={() => setOpen(null)}
        >
          The watcher stops reading this stack. Its runs, signatures and cases stay: they are the record of what it found.
        </ConfirmDialog>
      )}
    </div>
  );
}

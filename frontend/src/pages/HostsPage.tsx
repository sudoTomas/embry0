import { useState } from "react";
import { Server } from "lucide-react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { Host, HostSource } from "@/api/hosts";
import { useHosts, useImportTailnetHosts, useProbeHost, useRemoveHost, useUpdateHost } from "@/hooks/useHosts";
import { AdminButton } from "@/components/auth/AdminButton";
import { HostFormDialog } from "@/components/hosts/HostFormDialog";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Menu } from "@/components/ui/Menu";
import { Switch } from "@/components/ui/Switch";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { HOST_STATUS_TONES, TRANSPORT_LABELS } from "@/lib/hosts";
import { sentenceCase, statusLabel } from "@/lib/status";
import { useIsAdmin } from "@/stores/authStore";
import { formatDateTime } from "@/lib/utils";

type Open = { kind: "add" } | { kind: "edit"; host: Host } | { kind: "remove"; host: Host } | { kind: "import" } | null;

/**
 * Hosts (spec 3.7, issue Q): the machines that run the code embry0
 * maintains, from docs/hosts.md. Reads are open to every tier; probing is an
 * operator's; adding, editing, removing and the tailnet import are an admin's
 * and are offered disabled below that. The page's one gold is Add host.
 */
export function HostsPage() {
  const [source, setSource] = useState<HostSource | undefined>(undefined);
  const [includeRemoved, setIncludeRemoved] = useState(false);
  const [open, setOpen] = useState<Open>(null);
  const isAdmin = useIsAdmin();
  const list = useHosts({ source, includeRemoved });
  const probe = useProbeHost();
  const remove = useRemoveHost();
  const update = useUpdateHost();
  const importHosts = useImportTailnetHosts();

  const runProbe = (host: Host) =>
    probe.mutate(host.id, {
      onSuccess: ({ probe: result }) => {
        if (result.reachable) toast.success(`${host.name} is reachable: ${result.detail}`);
        else toast.error(`${host.name} is unreachable: ${result.detail}`);
      },
      onError: (e) => toastApiError(e, `Could not probe ${host.name}`),
    });

  const runImport = () =>
    importHosts.mutate(undefined, {
      onSuccess: (result) => {
        const c = result.counts;
        toast.success(`Imported from ${result.tailnet}: ${c.created} created, ${c.updated} updated, ${c.unchanged} unchanged, ${c.skipped} skipped`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, "Tailnet import failed");
      },
    });

  const runRemove = (host: Host) =>
    remove.mutate(host.id, {
      onSuccess: () => {
        toast.success(`Removed ${host.name}`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, `Could not remove ${host.name}`);
      },
    });

  const restore = (host: Host) =>
    update.mutate(
      { id: host.id, patch: { status: "active" } },
      {
        onSuccess: () => toast.success(`Restored ${host.name}`),
        onError: (e) => toastApiError(e, `Could not restore ${host.name}`),
      },
    );

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load hosts" onRetry={() => void list.refetch()} />;
  } else if (list.isPending || !list.data) {
    body = <TableSkeleton />;
  } else if (list.data.hosts.length === 0) {
    body = (
      <EmptyState
        icon={Server}
        title="No hosts yet"
        description="Add the machines that run your repositories' code by hand, or import them from a tailnet."
      />
    );
  } else {
    body = (
      // On a phone the transport, the roles, the source and the last-seen time step aside.
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Address</th>
              <th scope="col" className="hidden sm:table-cell">Transport</th>
              <th scope="col" className="hidden sm:table-cell">Roles</th>
              <th scope="col">Status</th>
              <th scope="col" className="hidden sm:table-cell">Source</th>
              <th scope="col" className="num hidden sm:table-cell">Last seen</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.hosts.map((host) => {
              const removed = host.status === "removed";
              return (
                <tr key={host.id} data-testid="host-row">
                  <td className="whitespace-nowrap font-medium">{host.name}</td>
                  <td className="w-full max-w-0 truncate font-mono text-xs text-fg-secondary">{host.address || "—"}</td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{TRANSPORT_LABELS[host.transport]}</td>
                  <td className="hidden sm:table-cell">
                    <span className="inline-flex flex-wrap gap-1">
                      {host.roles.map((role) => (
                        <Badge key={role} tone="neutral">
                          {role}
                        </Badge>
                      ))}
                    </span>
                  </td>
                  <td className="whitespace-nowrap">
                    <Badge tone={HOST_STATUS_TONES[host.status]}>{statusLabel(host.status)}</Badge>
                  </td>
                  <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{sentenceCase(host.source)}</td>
                  <td className="num hidden whitespace-nowrap text-fg-muted sm:table-cell">
                    {host.last_seen_at ? formatDateTime(host.last_seen_at) : "Never"}
                  </td>
                  <td className="w-px">
                    <Menu
                      label={`Actions for ${host.name}`}
                      items={[
                        { label: "Probe", onSelect: () => runProbe(host), disabled: removed || probe.isPending },
                        // A removed host's edit cannot land: the route refuses repository links until it is restored.
                        { label: "Edit", onSelect: () => setOpen({ kind: "edit", host }), disabled: !isAdmin || removed },
                        removed
                          ? { label: "Restore", onSelect: () => restore(host), disabled: !isAdmin }
                          : { label: "Remove", onSelect: () => setOpen({ kind: "remove", host }), destructive: true, disabled: !isAdmin },
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
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-xl font-bold">Hosts</h1>
          <p className="text-sm text-muted-foreground">The machines that run the code embry0 maintains, and how it reaches them.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AdminButton type="button" variant="outline" onClick={() => setOpen({ kind: "import" })} disabled={importHosts.isPending}>
            Import from tailnet
          </AdminButton>
          <AdminButton type="button" onClick={() => setOpen({ kind: "add" })}>
            Add host
          </AdminButton>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <FilterSelect
          label="Sources"
          value={source}
          options={list.data?.sources ?? ["manual", "tailnet"]}
          onChange={(v) => setSource(v as HostSource | undefined)}
          optionLabel={sentenceCase}
        />
        <div className="flex items-center gap-2">
          <Switch id="hosts-show-removed" checked={includeRemoved} onCheckedChange={setIncludeRemoved} label="Show removed" />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Show removed</span>
        </div>
      </div>
      {body}
      {(open?.kind === "add" || open?.kind === "edit") && (
        <HostFormDialog
          host={open.kind === "edit" ? open.host : undefined}
          logSourceKinds={list.data?.log_source_kinds ?? ["journald", "docker", "loki"]}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "remove" && (
        <ConfirmDialog
          title={`Remove ${open.host.name}?`}
          confirmLabel="Remove"
          destructive
          pending={remove.isPending}
          onConfirm={() => runRemove(open.host)}
          onClose={() => setOpen(null)}
        >
          Its repository links are dropped. The host stays as removed, so a tailnet import does not bring it back; Restore brings it back.
        </ConfirmDialog>
      )}
      {open?.kind === "import" && (
        <ConfirmDialog
          title="Import hosts from the tailnet?"
          confirmLabel="Import"
          pending={importHosts.isPending}
          onConfirm={runImport}
          onClose={() => setOpen(null)}
        >
          Creates one host per device of the tailnet and updates the ones already here by name. Removed hosts stay removed. Needs a tailnet API key in the credential registry.
        </ConfirmDialog>
      )}
    </div>
  );
}

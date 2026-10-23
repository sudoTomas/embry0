import { useState, type FormEvent } from "react";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { Host, HostTransport, HostWrite, LogSourceKind } from "@/api/hosts";
import { useCreateHost, useUpdateHost } from "@/hooks/useHosts";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { TagInput } from "@/components/ui/TagInput";
import { ADDRESS_HINTS, LOG_SOURCE_LABELS, TRANSPORT_LABELS } from "@/lib/hosts";

const TRANSPORTS: HostTransport[] = ["tailnet", "ssh", "http"];

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  if (response?.status === 409 || response?.status === 422) return formatErrorDetail(response.data?.detail) ?? "The host was not saved.";
  return null;
}

interface Props {
  /** The host to edit; absent, the form adds one. */
  host?: Host;
  logSourceKinds: LogSourceKind[];
  onClose: () => void;
}

/**
 * Add or edit a host (spec 3.7, "Hosts"): the editable record of
 * docs/hosts.md as one form. The server owns the address rules per transport
 * and the name's uniqueness; its 409 and 422 are shown under the form, which
 * stays open with what was typed.
 */
export function HostFormDialog({ host, logSourceKinds, onClose }: Props) {
  const [name, setName] = useState(host?.name ?? "");
  const [transport, setTransport] = useState<HostTransport>(host?.transport ?? "tailnet");
  const [address, setAddress] = useState(host?.address ?? "");
  const [roles, setRoles] = useState<string[]>(host?.roles ?? []);
  const [tags, setTags] = useState<string[]>(host?.tags ?? []);
  const [logKind, setLogKind] = useState<LogSourceKind | "">(host?.log_source?.kind ?? "");
  const [selector, setSelector] = useState(host?.log_source?.selector ?? "");
  const [repos, setRepos] = useState<string[]>(host?.repos.map((r) => r.repo) ?? []);
  const [error, setError] = useState<string | null>(null);
  const create = useCreateHost();
  const update = useUpdateHost();
  const pending = create.isPending || update.isPending;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name the host.");
      return;
    }
    if (logKind && !selector.trim()) {
      setError("Say what to read from the log source.");
      return;
    }
    setError(null);
    const body: HostWrite = {
      name: name.trim(),
      address: address.trim(),
      transport,
      roles,
      log_source: logKind ? { kind: logKind, selector: selector.trim() } : null,
      tags,
      // PATCH replaces the links, so each one keeps the role it has; a new one is linked as app.
      repos: repos.map((repo) => ({ repo, role: host?.repos.find((r) => r.repo === repo)?.role ?? "app" })),
    };
    const opts = {
      onSuccess: onClose,
      onError: (err: unknown) => {
        const shown = refusal(err);
        if (shown) setError(shown);
        else toastApiError(err, host ? "The host was not saved" : "The host was not added");
      },
    };
    if (host) update.mutate({ id: host.id, patch: body }, opts);
    else create.mutate(body, opts);
  };

  return (
    <Dialog onClose={() => !pending && onClose()} title={host ? `Edit ${host.name}` : "Add host"} dismissOnBackdrop={false} className="max-w-xl">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-name">Name</Label>
            <Input id="host-name" value={name} onChange={(e) => setName(e.target.value)} disabled={pending} data-autofocus />
            <p className="text-xs text-fg-muted">Letters, digits, dots, underscores and dashes; unique.</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="host-transport">Transport</Label>
            <Select id="host-transport" value={transport} onChange={(e) => setTransport(e.target.value as HostTransport)} disabled={pending}>
              {TRANSPORTS.map((t) => (
                <option key={t} value={t}>
                  {TRANSPORT_LABELS[t]}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="host-address">Address</Label>
          <Input id="host-address" value={address} onChange={(e) => setAddress(e.target.value)} disabled={pending} />
          <p className="text-xs text-fg-muted">{ADDRESS_HINTS[transport]}</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-roles">Roles</Label>
            <TagInput id="host-roles" value={roles} onChange={setRoles} placeholder="app, db, worker…" disabled={pending} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="host-tags">Tags</Label>
            <TagInput id="host-tags" value={tags} onChange={setTags} placeholder="Add a tag…" disabled={pending} />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="host-log-kind">Log source</Label>
            <Select id="host-log-kind" value={logKind} onChange={(e) => setLogKind(e.target.value as LogSourceKind | "")} disabled={pending}>
              <option value="">None</option>
              {logSourceKinds.map((k) => (
                <option key={k} value={k}>
                  {LOG_SOURCE_LABELS[k]}
                </option>
              ))}
            </Select>
          </div>
          {logKind && (
            <div className="space-y-1">
              <Label htmlFor="host-log-selector">Selector</Label>
              <Input id="host-log-selector" value={selector} onChange={(e) => setSelector(e.target.value)} disabled={pending} />
              <p className="text-xs text-fg-muted">
                {logKind === "journald" ? "A systemd unit." : logKind === "docker" ? "A container name." : "A LogQL selector."}
              </p>
            </div>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="host-repos">Repositories</Label>
          <TagInput id="host-repos" value={repos} onChange={setRepos} placeholder="owner/name…" disabled={pending} />
          <p className="text-xs text-fg-muted">The repositories this host runs. New repositories are linked with the role app.</p>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : host ? "Save" : "Add host"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { Organization, OrgStatus } from "@/api/orgs";
import { useInstallOrganizationWebhook, useOrganizationWebhook, useSetOrganizationStatus, useUpdateOrganization } from "@/hooks/useOrgs";
import { Badge } from "@/components/ui/Badge";
import { Button, type ButtonProps } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Switch } from "@/components/ui/Switch";
import { Tooltip } from "@/components/ui/Tooltip";
import { statusLabel } from "@/lib/status";

function refusal(err: unknown, fallback: string): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  return formatErrorDetail(response?.data?.detail) ?? fallback;
}

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="space-y-2 rounded-lg border border-border p-4" data-testid={testId}>
      <h2 className="text-section font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** An admin's button, disabled with the reason on hover for everyone else, in the page's non-gold variants. */
function Gated({ isAdmin, children, ...props }: { isAdmin: boolean; children: ReactNode } & ButtonProps) {
  const button = (
    <Button type="button" size="sm" {...props} variant={props.variant ?? "outline"} disabled={props.disabled || !isAdmin}>
      {children}
    </Button>
  );
  return isAdmin ? button : <Tooltip content="Admin scope required">{button}</Tooltip>;
}

/**
 * An organization's settings (spec 3.7, issue Q): its display name, whether a
 * webhook delivery registers a repository nobody registered, the webhook on
 * the organization itself, and its status. Every write is an admin's; below
 * that the controls are shown disabled. Nothing here is gold: the page's one
 * gold is Apply in the act panel.
 */
export function OrgSettingsTab({ org, isAdmin }: { org: Organization; isAdmin: boolean }) {
  const [name, setName] = useState(org.display_name ?? "");
  const [nameError, setNameError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const update = useUpdateOrganization(org.owner);
  const setStatus = useSetOrganizationStatus(org.owner);
  const install = useInstallOrganizationWebhook(org.owner);
  const github = org.forge === "github";
  const webhook = useOrganizationWebhook(org.owner, github);
  const autoRegister = org.settings.auto_register === true;

  const saveName = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    update.mutate(
      { display_name: trimmed === "" ? null : trimmed },
      { onSuccess: () => setNameError(null), onError: (err) => setNameError(refusal(err, "The name was not saved.")) },
    );
  };

  const toggleAutoRegister = (next: boolean) =>
    update.mutate({ settings: { auto_register: next } }, { onError: (err) => toastApiError(err, "The setting was not saved") });

  const changeStatus = (status: OrgStatus) =>
    setStatus.mutate(status, {
      onSuccess: (saved) => {
        setRemoving(false);
        toast.success(`${saved.display_name || saved.owner} is now ${statusLabel(saved.status).toLowerCase()}`);
      },
      onError: (err) => {
        setRemoving(false);
        toastApiError(err, "The status was not changed");
      },
    });

  // The route is idempotent: with a hook already pointing here it answers
  // "already", so the button stays offered and the answer is what is toasted.
  const installHook = () =>
    install.mutate(undefined, {
      onSuccess: (result) => {
        if (result.outcome === "done") toast.success("Webhook installed on the organization.");
        else toast.info("A webhook already points at this deployment.");
      },
      onError: (err) => toastApiError(err, "The webhook was not installed"),
    });

  return (
    <div className="space-y-4" data-testid="org-settings">
      <Section title="Name">
        <form onSubmit={saveName} className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="org-display-name">Display name</Label>
            <Input id="org-display-name" value={name} onChange={(e) => setName(e.target.value)} disabled={!isAdmin || update.isPending} placeholder={org.owner} className="w-64" />
          </div>
          <Gated isAdmin={isAdmin} type="submit" disabled={update.isPending}>
            Save name
          </Gated>
          <span className="text-xs text-fg-muted">Empty shows the owner's own name, {org.owner}.</span>
        </form>
        {nameError && (
          <p className="text-sm text-destructive" role="alert">
            {nameError}
          </p>
        )}
      </Section>

      <Section title="Automatic registration">
        <div className="flex items-center gap-3">
          <Switch
            id="org-auto-register"
            checked={autoRegister}
            onCheckedChange={toggleAutoRegister}
            disabled={!isAdmin || update.isPending}
            label="Register repositories automatically"
          />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Register repositories automatically</span>
        </div>
        <p className="text-xs text-fg-muted">
          A webhook delivery for a repository under {org.owner} that nobody registered registers it, bound to this organization's credential, before the delivery is handled. Off, the need is recorded instead.
        </p>
      </Section>

      <Section title="Webhook" testId="org-webhook">
        {!github ? (
          <p className="text-sm text-fg-muted">Organization webhooks are a GitHub feature. A GitLab group's repositories each carry their own.</p>
        ) : webhook.isError ? (
          <p className="text-sm text-destructive">The webhook could not be read from GitHub.</p>
        ) : !webhook.data ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Badge tone={webhook.data.installed ? "success" : "neutral"}>{webhook.data.installed ? "Installed" : "Not installed"}</Badge>
              {webhook.data.target_url ? (
                <span className="font-mono text-xs text-fg-secondary">{webhook.data.target_url}</span>
              ) : (
                <span className="text-fg-muted">No public webhook origin is configured.</span>
              )}
              <Gated isAdmin={isAdmin} onClick={installHook} disabled={install.isPending || org.status !== "active"}>
                {install.isPending ? "Installing…" : "Install webhook"}
              </Gated>
            </div>
            {!webhook.data.hooks_visible ? (
              <p className="text-xs text-fg-muted">The credential cannot list the organization's hooks.</p>
            ) : webhook.data.hooks.length === 0 ? (
              <p className="text-xs text-fg-muted">No webhooks on the organization.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {webhook.data.hooks.map((hook) => (
                  <li key={hook.id ?? hook.url} className="flex flex-wrap items-center gap-2 font-mono text-fg-secondary">
                    <span>{hook.url}</span>
                    <span className="text-fg-muted">{`${hook.events.length} ${hook.events.length === 1 ? "event" : "events"}${hook.active ? "" : ", inactive"}`}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </Section>

      <Section title="Status">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={org.status === "active" ? "success" : org.status === "paused" ? "warning" : "neutral"}>{statusLabel(org.status)}</Badge>
          {org.status === "active" && (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("paused")} disabled={setStatus.isPending}>
              Pause
            </Gated>
          )}
          {org.status === "paused" && (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("active")} disabled={setStatus.isPending}>
              Resume
            </Gated>
          )}
          {org.status === "removed" ? (
            <Gated isAdmin={isAdmin} onClick={() => changeStatus("active")} disabled={setStatus.isPending}>
              Restore
            </Gated>
          ) : (
            <Gated isAdmin={isAdmin} variant="destructive" onClick={() => setRemoving(true)} disabled={setStatus.isPending}>
              Remove organization
            </Gated>
          )}
        </div>
        <p className="text-xs text-fg-muted">
          Paused, discovery and the schedule stop and the webhook gate records needs without acting. Removed, the owner leaves the lists; Restore brings it back.
        </p>
      </Section>

      {removing && (
        <ConfirmDialog
          title={`Remove ${org.owner}?`}
          confirmLabel="Remove"
          destructive
          pending={setStatus.isPending}
          onConfirm={() => changeStatus("removed")}
          onClose={() => setRemoving(false)}
        >
          The owner leaves the organization lists and discovery stops. Its repositories stay registered and keep working; Restore on this tab brings the owner back.
        </ConfirmDialog>
      )}
    </div>
  );
}

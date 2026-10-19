import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { detailMessages } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Tooltip } from "@/components/ui/Tooltip";
import { useCredentials, useScopedBindings, useSetScopedBindings } from "@/hooks/useCredentials";
import { ROLE_FOR_KIND, SCOPES_FOR_ROLE } from "@/lib/bindingRoles";
import { createFocusTrap } from "@/lib/focus-trap";
import { cn } from "@/lib/utils";
import type { Binding, BindingInput, BindingRole, Credential, ScopeKind } from "@/lib/types/credentials";

type DrawerScope = Exclude<ScopeKind, "repo">;

/** The scopes this drawer edits, in the order it offers them. Repo bindings live on the repo page. */
const DRAWER_SCOPES: readonly DrawerScope[] = ["global", "owner", "host"];
const SCOPE_LABELS: Record<DrawerScope, string> = { global: "Global", owner: "Owner", host: "Host" };

export interface BindingsDrawerProps {
  credential: Credential;
  isAdmin: boolean;
  onClose(): void;
}

/**
 * An owner scope is always '<forge>:<owner>'. A forge token's is on its own
 * forge; an LLM account's is looked up under github: and gitlab: alike
 * (CredentialResolver._resolve_llm_fresh), so it starts at github:.
 */
function prefill(kind: DrawerScope, credential: Credential, role: BindingRole): string {
  if (kind === "owner") return role === "forge" ? `${credential.provider}:` : "github:";
  if (kind === "host") return credential.host;
  return "";
}

function asScope(value: string, offered: readonly DrawerScope[]): DrawerScope {
  return offered.find((scope) => scope === value) ?? offered[0];
}

function toInput(binding: Binding): BindingInput {
  const input: BindingInput = { credential_id: binding.credential_id, role: binding.role };
  if (binding.pinned) input.pinned = true;
  if (typeof binding.priority === "number" && binding.priority !== 0) input.priority = binding.priority;
  return input;
}

/** 422 detail string, or `msg` entries from a 422 array. Never reads `input`. */
function alertFromError(error: unknown): string | null {
  if (!error || typeof error !== "object" || !("response" in error)) return null;
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  if (response?.status !== 422) return null;
  const parts = detailMessages(response.data?.detail);
  return parts.length > 0 ? parts.join(" ") : null;
}

function AdminFields({ isAdmin, children }: { isAdmin: boolean; children: ReactNode }) {
  const fields = (
    <fieldset
      disabled={!isAdmin}
      className={cn("m-0 min-w-0 space-y-4 border-0 p-0", !isAdmin && "pointer-events-none")}
    >
      {children}
    </fieldset>
  );
  if (isAdmin) return fields;
  // Tooltip's own wrapper is inline-flex and would shrink the fields.
  return (
    <div className="w-full [&>div]:w-full">
      <Tooltip content="Admin scope required">{fields}</Tooltip>
    </div>
  );
}

/**
 * PUT /credentials/bindings replaces every row of one scope. Save sends the
 * other credentials' rows plus this one, at the role its kind binds with.
 * Remove sends that scope without this credential. `useScopedBindings` is
 * keyed by the value we send, so its data is that scope after the server
 * normalizes the lookup.
 */
export function BindingsDrawer({ credential, isAdmin, onClose }: BindingsDrawerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const role = ROLE_FOR_KIND[credential.kind];
  const scopes = DRAWER_SCOPES.filter((scope) => SCOPES_FOR_ROLE[role].includes(scope));
  const [scopeKind, setScopeKind] = useState<DrawerScope>(scopes[0]);
  const [scopeValue, setScopeValue] = useState(() => prefill(scopes[0], credential, role));

  const scopeValueForApi = scopeValue.trim();
  const credentialsQuery = useCredentials();
  const bindingsQuery = useScopedBindings(scopeKind, scopeValueForApi);
  const mutation = useSetScopedBindings();

  const rows = Array.isArray(bindingsQuery.data) ? bindingsQuery.data : null;
  const ready = rows !== null && bindingsQuery.isFetching !== true;
  const mine = rows?.filter((binding) => binding.credential_id === credential.id) ?? [];
  const listed = credentialsQuery.data?.find((row) => row.id === credential.id);
  const bindingCount = listed?.binding_count ?? credential.binding_count;
  const formError = alertFromError(mutation.error);
  const writeLocked = !isAdmin || !ready || mutation.isPending;

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  function bindingLabel(binding: Binding): string {
    if (binding.label) return binding.label;
    if (binding.credential_id === credential.id) return credential.label;
    const known = credentialsQuery.data?.find((row) => row.id === binding.credential_id);
    return known?.label ?? binding.credential_id;
  }

  function onKind(value: string) {
    const next = asScope(value, scopes);
    setScopeKind(next);
    setScopeValue(prefill(next, credential, role));
  }

  function onAdd(event: FormEvent) {
    event.preventDefault();
    if (writeLocked || rows === null) return;
    mutation.mutate({
      scope_kind: scopeKind,
      scope_value: scopeValueForApi,
      bindings: [
        ...rows.filter((binding) => binding.credential_id !== credential.id).map(toInput),
        { credential_id: credential.id, role },
      ],
    });
  }

  function onRemove() {
    if (writeLocked || rows === null) return;
    mutation.mutate({
      scope_kind: scopeKind,
      scope_value: scopeValueForApi,
      bindings: rows.filter((binding) => binding.credential_id !== credential.id).map(toInput),
    });
  }

  const title = `Bindings for ${credential.label}`;
  const lockClass = !isAdmin ? "pointer-events-none" : undefined;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="relative h-full w-full max-w-md overflow-y-auto border-l border-border bg-card p-6 shadow-xl"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="pr-8 text-section font-semibold">{title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          {bindingCount} {bindingCount === 1 ? "binding" : "bindings"} on this credential. The list
          below is only the scope in the form.
        </p>

        <div className="mt-5">
          {rows === null && bindingsQuery.isError && (
            <p role="alert" className="text-sm text-destructive">Could not load bindings.</p>
          )}
          {rows === null && !bindingsQuery.isError && (
            <p className="text-sm text-muted-foreground">Loading bindings...</p>
          )}
          {rows !== null && mine.length === 0 && (
            <p className="text-sm text-muted-foreground">Not bound at this scope.</p>
          )}
          {rows !== null && rows.length > 0 && (
            <ul className="space-y-2 text-sm">
              {rows.map((binding) => (
                <li key={`${binding.credential_id}:${binding.role}`}>
                  <span className="text-foreground">{bindingLabel(binding)}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    {binding.scope_kind}
                    {binding.scope_value ? ` ${binding.scope_value}` : ""} {binding.role}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {mine.length > 0 && (
            <div className="mt-3">
              {isAdmin ? (
                <Button type="button" variant="outline" onClick={onRemove} disabled={writeLocked}>
                  Remove from this scope
                </Button>
              ) : (
                <Tooltip content="Admin scope required">
                  <Button type="button" variant="outline" disabled className="pointer-events-none">
                    Remove from this scope
                  </Button>
                </Tooltip>
              )}
            </div>
          )}
        </div>

        <form onSubmit={onAdd} noValidate autoComplete="off" className="mt-6 space-y-4">
          <h3 className="text-sm font-medium">Add binding</h3>
          <AdminFields isAdmin={isAdmin}>
            <div>
              <Label htmlFor="binding-scope-kind">Scope kind</Label>
              <Select
                id="binding-scope-kind"
                className={cn("mt-1", lockClass)}
                value={scopeKind}
                disabled={!isAdmin}
                onChange={(event) => onKind(event.target.value)}
              >
                {scopes.map((scope) => (
                  <option key={scope} value={scope}>
                    {SCOPE_LABELS[scope]}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="binding-scope-value">Scope value</Label>
              <Input
                id="binding-scope-value"
                className={cn("mt-1 font-mono", lockClass)}
                value={scopeValue}
                disabled={!isAdmin}
                autoComplete="off"
                aria-describedby="binding-scope-hint"
                onChange={(event) => setScopeValue(event.target.value)}
              />
              <p id="binding-scope-hint" className="mt-1 text-xs text-muted-foreground">
                {scopeKind === "global" && "Global scope uses an empty value."}
                {scopeKind === "owner" && "Owner is github:name or gitlab:name, for example github:acme."}
                {scopeKind === "host" && "Host is the credential host."}
              </p>
            </div>
            <p className="text-sm text-muted-foreground">Role is {role}.</p>
            <p className="text-xs text-muted-foreground">
              Saving keeps other credentials already bound at this scope.
            </p>
            <div className="flex justify-end">
              <Button type="submit" disabled={writeLocked}>
                Add binding
              </Button>
            </div>
          </AdminFields>
        </form>

        {formError && <p role="alert" className="mt-4 text-sm text-destructive">{formError}</p>}
      </div>
    </div>
  );
}

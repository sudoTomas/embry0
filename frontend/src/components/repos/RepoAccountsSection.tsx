import { useEffect, useState, type ChangeEvent, type ReactNode } from "react";
import { toast } from "sonner";
import { detailMessages, isReported } from "@/api/errorDetail";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Tooltip } from "@/components/ui/Tooltip";
import { useCredentials, useRepoCredentials, useSetRepoCredentials } from "@/hooks/useCredentials";
import { useModelsCatalog } from "@/hooks/useProvider";
import { ENABLE_SETTING, llmProviderStates, usableLlmProviders, type LlmProviderName } from "@/lib/llmProviders";
import type { Binding, BindingInput, RepoCredentials } from "@/lib/types/credentials";
import { providerLabel } from "@/lib/status";
import { useAuthStore } from "@/stores/authStore";

const NONE_VALUE = "";
const NONE_LABEL = "Use the shared account (recommended)";
const ENV_SOURCE_TITLE = "Comes from the server's settings, not the account list";
const PIN_COPY = "If this account is paused, stop work on this repository instead of using the shared account";

type ForgeName = "github" | "gitlab";
type ForgeEffective = NonNullable<RepoCredentials["effective"]["forge"]>;

export function RepoAccountsSection({ repo, forge }: { repo: string; forge: ForgeName }) {
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const repoQuery = useRepoCredentials(repo);
  const credentialsQuery = useCredentials();
  const setRepoCredentials = useSetRepoCredentials();
  // The providers page's catalog: a row per provider this deployment enables.
  const catalog = useModelsCatalog();
  // The select is controlled by the server. Keep a local choice until that
  // query refetches, and drop it if the write fails.
  const [draft, setDraft] = useState<string | null>(null);
  const [llmDraft, setLlmDraft] = useState<Partial<Record<LlmProviderName, string>>>({});
  const [pinDraft, setPinDraft] = useState<Partial<Record<LlmProviderName, boolean>>>({});

  useEffect(() => {
    setDraft(null);
    setLlmDraft({});
    setPinDraft({});
  }, [repoQuery.data]);

  if (repoQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading accounts...</p>;
  }
  if (repoQuery.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load repo accounts.
      </p>
    );
  }

  const bindings = Array.isArray(repoQuery.data?.bindings) ? repoQuery.data.bindings : [];
  const credentials = Array.isArray(credentialsQuery.data) ? credentialsQuery.data : [];
  const options = credentials.filter((credential) => credential.provider === forge);
  const forgeBinding = bindings.find((binding) => binding.role === "forge");
  const serverSelected =
    forgeBinding && options.some((credential) => credential.id === forgeBinding.credential_id)
      ? forgeBinding.credential_id
      : NONE_VALUE;
  const selected = draft ?? serverSelected;

  function onBoundAccount(event: ChangeEvent<HTMLSelectElement>) {
    if (!isAdmin || setRepoCredentials.isPending) return;
    const credentialId = event.target.value;
    setDraft(credentialId);
    const kept = bindings.filter((binding) => binding.role !== "forge").map(keptInput);
    const next =
      credentialId === NONE_VALUE
        ? kept
        : [...kept, { credential_id: credentialId, role: "forge" as const }];
    setRepoCredentials.mutate(
      { fullRepo: repo, bindings: next },
      {
        onSuccess: () => {
          toast.success(credentialId === NONE_VALUE ? "Forge account cleared" : "Forge account bound");
        },
        onError: (error: unknown) => {
          setDraft(null);
          if (isReported(error)) return;
          toast.error(failureText(error));
        },
      },
    );
  }

  const select = (
    <Select
      id="repo-bound-account"
      value={selected}
      disabled={!isAdmin || setRepoCredentials.isPending}
      className={!isAdmin ? "pointer-events-none" : undefined}
      onChange={onBoundAccount}
    >
      <option value={NONE_VALUE}>{NONE_LABEL}</option>
      {options.map((credential) => (
        <option key={credential.id} value={credential.id}>
          {credential.label}
        </option>
      ))}
    </Select>
  );

  function providerOf(binding: Binding): string | undefined {
    return binding.provider ?? credentials.find((row) => row.id === binding.credential_id)?.provider;
  }

  function selectedFor(provider: LlmProviderName): string {
    const drafted = llmDraft[provider];
    if (drafted !== undefined) return drafted;
    const binding = bindings.find((row) => row.role === "llm" && providerOf(row) === provider);
    if (binding && credentials.some((row) => row.id === binding.credential_id && row.provider === provider)) {
      return binding.credential_id;
    }
    return NONE_VALUE;
  }

  function pinnedFor(provider: LlmProviderName): boolean {
    if (pinDraft[provider] !== undefined) return Boolean(pinDraft[provider]);
    const binding = bindings.find((row) => row.role === "llm" && providerOf(row) === provider);
    return Boolean(binding?.pinned);
  }

  function writeLlm(provider: LlmProviderName, credentialId: string, pinned: boolean) {
    if (!isAdmin || setRepoCredentials.isPending) return;
    setLlmDraft((curr) => ({ ...curr, [provider]: credentialId }));
    setPinDraft((curr) => ({ ...curr, [provider]: pinned }));
    const kept = bindings
      .filter((binding) => !(binding.role === "llm" && providerOf(binding) === provider))
      .map(keptInput);
    const next =
      credentialId === NONE_VALUE
        ? kept
        : [...kept, { credential_id: credentialId, role: "llm" as const, pinned }];
    setRepoCredentials.mutate(
      { fullRepo: repo, bindings: next },
      {
        onSuccess: () => {
          toast.success(credentialId === NONE_VALUE ? "LLM account cleared" : "LLM account bound");
        },
        onError: (error: unknown) => {
          setLlmDraft((curr) => {
            const copy = { ...curr };
            delete copy[provider];
            return copy;
          });
          setPinDraft((curr) => {
            const copy = { ...curr };
            delete copy[provider];
            return copy;
          });
          if (isReported(error)) return;
          toast.error(failureText(error));
        },
      },
    );
  }

  const llm = repoQuery.data?.effective?.llm;
  const llmStates = llmProviderStates(catalog.data?.providers);
  // A row only for a provider this repository can use; the rest are counted.
  const llmRows = usableLlmProviders(
    catalog.data?.providers,
    bindings.map((binding) => ({ role: binding.role, provider: providerOf(binding) })),
    llm,
  );
  const unused = llmStates.filter((entry) => entry.state === "enabled" && !llmRows.includes(entry.name)).length;
  const notEnabled = llmStates.filter((entry) => entry.state === "not_enabled").map((entry) => entry.name);

  return (
    <div className="space-y-4">
      <Card className="space-y-2 p-4">
        <h3 className="text-sm font-medium">Effective forge account</h3>
        <ForgeAccount forge={repoQuery.data?.effective?.forge} />
      </Card>
      <div className="space-y-1">
        <Label htmlFor="repo-bound-account">{providerLabel(forge)} account embry0 uses for this repository</Label>
        <AdminPicker isAdmin={isAdmin}>{select}</AdminPicker>
      </div>
      <Card className="space-y-4 p-4">
        <h3 className="text-sm font-medium">LLM accounts</h3>
        {llmRows.map((provider) => (
          <LlmProviderRow
            key={provider}
            provider={provider}
            effective={llm?.[provider]}
            options={credentials.filter((credential) => credential.provider === provider)}
            selected={selectedFor(provider)}
            pinned={pinnedFor(provider)}
            isAdmin={isAdmin}
            pending={setRepoCredentials.isPending}
            onSelect={(credentialId) => writeLlm(provider, credentialId, pinnedFor(provider))}
            onPin={(pinned) => {
              const credentialId = selectedFor(provider);
              if (!credentialId) return;
              writeLlm(provider, credentialId, pinned);
            }}
          />
        ))}
        {unused > 0 && (
          <p data-testid="llm-unused" className="text-xs text-muted-foreground">
            {unused === 1
              ? "1 other AI company is switched on here but has no account. This repository does not use it."
              : `${unused} other AI companies are switched on here but have no account. This repository does not use them.`}
          </p>
        )}
        {notEnabled.length > 0 && (
          <p data-testid="llm-not-enabled" className="text-xs text-muted-foreground">
            Not enabled in this deployment: {notEnabled.map(providerLabel).join(", ")}. Name a provider in{" "}
            <span className="font-mono">{ENABLE_SETTING}</span> to bind its account here.
          </p>
        )}
      </Card>
    </div>
  );
}

/** PUT replaces every repo binding, so other roles stay and only the forge row changes. */
function keptInput(binding: Binding): BindingInput {
  return {
    credential_id: binding.credential_id,
    role: binding.role,
    pinned: binding.pinned,
    priority: binding.priority,
  };
}

function LlmProviderRow({
  provider,
  effective,
  options,
  selected,
  pinned,
  isAdmin,
  pending,
  onSelect,
  onPin,
}: {
  provider: LlmProviderName;
  effective: RepoCredentials["effective"]["llm"] extends infer T
    ? T extends Record<string, infer V>
      ? V
      : undefined
    : undefined;
  options: { id: string; label: string }[];
  selected: string;
  pinned: boolean;
  isAdmin: boolean;
  pending: boolean;
  onSelect(credentialId: string): void;
  onPin(pinned: boolean): void;
}) {
  const label = `${providerLabel(provider)} account`;
  const selectId = `repo-llm-${provider}`;
  const select = (
    <Select
      id={selectId}
      value={selected}
      disabled={!isAdmin || pending}
      className={!isAdmin ? "pointer-events-none" : undefined}
      onChange={(event) => onSelect(event.target.value)}
    >
      <option value={NONE_VALUE}>{NONE_LABEL}</option>
      {options.map((credential) => (
        <option key={credential.id} value={credential.id}>
          {credential.label}
        </option>
      ))}
    </Select>
  );
  return (
    <div data-testid={`llm-${provider}`} className="space-y-2">
      <LlmAccount value={effective} provider={provider} />
      <div className="space-y-1">
        <Label htmlFor={selectId}>{label}</Label>
        <AdminPicker isAdmin={isAdmin}>{select}</AdminPicker>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={pinned}
            disabled={!isAdmin || pending || selected === NONE_VALUE}
            onChange={(event) => onPin(event.target.checked)}
          />
          {PIN_COPY}
        </label>
      </div>
    </div>
  );
}

function LlmAccount({
  value,
  provider,
}: {
  value:
    | {
        id?: string | null;
        label?: string;
        source?: string;
        trace?: string[];
        error?: string;
      }
    | undefined;
  provider: LlmProviderName;
}) {
  if (value == null) {
    return <p className="text-sm text-muted-foreground">No {providerLabel(provider)} account resolved.</p>;
  }
  if ("error" in value && value.error) {
    return <p className="text-sm text-destructive">{value.error}</p>;
  }
  const source = value.source ?? "";
  const trace = Array.isArray(value.trace) ? value.trace : [];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs">{value.label ?? ""}</span>
        {source && (
          <Badge tone={sourceTone(source)} title={source === "env" ? ENV_SOURCE_TITLE : undefined}>
            {source}
          </Badge>
        )}
      </div>
      <details data-testid={`llm-trace-${provider}`}>
        <summary className="cursor-pointer text-sm text-muted-foreground">Why this account?</summary>
        <ul className="mt-1 list-disc space-y-1 pl-5 font-mono text-xs text-fg-secondary">
          {trace.map((line, index) => (
            <li key={`${index}:${line}`}>{line}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function AdminPicker({ isAdmin, children }: { isAdmin: boolean; children: ReactNode }) {
  if (isAdmin) return children;
  return (
    <div className="w-full [&>div]:w-full">
      <Tooltip content="Only an admin can change accounts.">{children}</Tooltip>
    </div>
  );
}

function sourceTone(source: string): BadgeTone {
  if (source === "registry") return "success";
  if (source === "env") return "warning";
  return "neutral";
}

function ForgeAccount({ forge }: { forge: RepoCredentials["effective"]["forge"] }) {
  if (forge == null || typeof forge !== "object") {
    return <p className="text-sm text-muted-foreground">No forge account resolved.</p>;
  }
  if ("error" in forge) {
    const text = typeof forge.error === "string" ? forge.error : "Forge account could not be resolved.";
    return (
      <p data-testid="repo-accounts-error" className="text-sm text-destructive">
        {text}
      </p>
    );
  }
  return <ResolvedForge forge={forge} />;
}

function ResolvedForge({ forge }: { forge: Exclude<ForgeEffective, { error: string }> }) {
  const source = forge.source ?? "";
  const trace = Array.isArray(forge.trace) ? forge.trace : [];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs">{forge.label ?? ""}</span>
        <Badge tone={sourceTone(source)} title={source === "env" ? ENV_SOURCE_TITLE : undefined}>
          {source}
        </Badge>
      </div>
      <details data-testid="repo-accounts-trace">
        <summary className="cursor-pointer text-sm text-muted-foreground">Why this account?</summary>
        <ul className="mt-1 list-disc space-y-1 pl-5 font-mono text-xs text-fg-secondary">
          {trace.map((line, index) => (
            <li key={`${index}:${line}`}>{line}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function failureText(error: unknown): string {
  if (error && typeof error === "object" && "response" in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    const parts = detailMessages(detail);
    if (parts.length > 0) return parts.join(" ");
  }
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  return "Failed to update the bound account";
}

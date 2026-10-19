import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { PageError } from "@/components/PageError";
import { formatDate } from "@/lib/utils";
import { providerLabel, sentenceCase } from "@/lib/status";
import { useAuthStore } from "@/stores/authStore";
import {
  useModelsCatalog,
  useProviderConfig,
  useTestCatalogProvider,
  useUpdateProviderConfig,
} from "@/hooks/useProvider";
import type { CatalogProvider, ProviderProbeResult } from "@/lib/types/provider";
import { toastApiError } from "@/api/errorToast";

// RAV-1297: LLM Providers — the complete catalog (Anthropic first-class),
// live connection tests, per-model enablement, and tier assignment. This
// page replaces the invisible-DB-state era: tier edits moved here from
// Settings → General.
// #465: the default model is a real setting — every agent resolves to it
// unless a pipeline template or the job names another model.

const TIER_FIELDS = [
  { key: "model_heavy", label: "Heavy" },
  { key: "model_medium", label: "Medium" },
  { key: "model_light", label: "Light" },
] as const;

type TierKey = (typeof TIER_FIELDS)[number]["key"];

/** Where a provider's credential comes from (`credential_source`), in words. */
const CREDENTIAL_SOURCE_LABELS: Record<string, string> = {
  oauth: "OAuth token",
  api_key: "API key",
  "oauth+api_key": "OAuth token and API key",
  oauth_proxy: "OAuth proxy",
  registry: "Credential registry",
};

const PROBE_STATUS_LABELS: Record<string, string> = {
  ok: "Connected",
  unauthorized: "Unauthorized",
  entitlement_refused: "Entitlement refused",
  unreachable: "Unreachable",
  not_configured: "Not configured",
  error: "Error",
};

function fmtPrice(v: number | null): string {
  return v == null ? "—" : `$${v.toFixed(2)}`;
}

function tierValue(catalog: { heavy: string; medium: string; light: string }, key: TierKey): string {
  if (key === "model_heavy") return catalog.heavy;
  if (key === "model_medium") return catalog.medium;
  return catalog.light;
}

function ProviderCard({
  provider,
  disabledModels,
  tierModels,
  defaultModel,
  isAdmin,
  onToggleModel,
  pending,
}: {
  provider: CatalogProvider;
  disabledModels: string[];
  tierModels: string[];
  defaultModel: string;
  isAdmin: boolean;
  onToggleModel: (model: string, enable: boolean) => void;
  pending: boolean;
}) {
  const testProvider = useTestCatalogProvider();
  const [probe, setProbe] = useState<ProviderProbeResult | null>(null);

  const handleTest = () => {
    testProvider.mutate(provider.name, {
      onSuccess: (result) => {
        setProbe(result);
        if (result.status === "ok") toast.success(`${providerLabel(provider.name)}: connection OK`);
        else toast.error(`${providerLabel(provider.name)}: ${PROBE_STATUS_LABELS[result.status] ?? result.status}`);
      },
      onError: (e) => toastApiError(e, "Test failed"),
    });
  };

  return (
    <Card data-testid={`provider-card-${provider.name}`}>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-section">{providerLabel(provider.name)}</CardTitle>
            {provider.default && <Badge tone="neutral">Default provider</Badge>}
            {provider.available ? (
              <Badge tone="success">Available</Badge>
            ) : (
              <Badge tone="error">Unavailable</Badge>
            )}
          </div>
          <Button variant="outline" size="sm" onClick={handleTest} disabled={testProvider.isPending}>
            {testProvider.isPending ? "Testing..." : "Test connection"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground font-mono">{provider.base_url}</p>
        <p className="text-xs text-muted-foreground">
          Credentials:{" "}
          {provider.credential_source ? (
            <span title={provider.credential_source}>
              {CREDENTIAL_SOURCE_LABELS[provider.credential_source] ?? sentenceCase(provider.credential_source)}
            </span>
          ) : (
            "none configured"
          )}
        </p>
        {provider.last_probe !== undefined && (
          <p className="text-xs text-muted-foreground" data-testid="provider-last-probe">
            Last test:{" "}
            {provider.last_probe ? (
              <>
                {PROBE_STATUS_LABELS[provider.last_probe.status] ?? provider.last_probe.status},{" "}
                <time dateTime={provider.last_probe.checked_at}>{formatDate(provider.last_probe.checked_at)}</time>
              </>
            ) : (
              "never"
            )}
          </p>
        )}
      </CardHeader>
      <CardContent>
        {probe && (
          <div
            className="mb-4 space-y-1 rounded-md border border-white/[0.06] p-3"
            data-testid="probe-results"
          >
            {probe.checks.map((c) => (
              <div key={c.source} className="flex items-start gap-2 text-sm">
                <Badge tone={c.status === "ok" ? "success" : "warning"}>
                  {PROBE_STATUS_LABELS[c.status] ?? c.status}
                </Badge>
                <span className="font-mono text-xs text-muted-foreground">{c.source}</span>
                <span className="text-muted-foreground">{c.message}</span>
              </div>
            ))}
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Model</th>
                <th className="hidden sm:table-cell">Input $/Mtok</th>
                <th className="hidden sm:table-cell">Output $/Mtok</th>
                <th className="text-right">Enabled</th>
              </tr>
            </thead>
            <tbody>
              {provider.models.map((m) => {
                const isTier = tierModels.includes(m.model);
                const isDefault = defaultModel !== "" && m.model === defaultModel;
                const enabled = !disabledModels.includes(m.model);
                // Backend interlock: a model that a tier or the default model
                // points at cannot be disabled — reassign it first.
                const locked = (isTier || isDefault) && enabled;
                return (
                  <tr key={m.model}>
                    <td className="font-mono">
                      {m.model}
                      {isTier && (
                        <span className="ml-2 text-xs text-muted-foreground">(tier-assigned)</span>
                      )}
                      {isDefault && (
                        <span className="ml-2 text-xs text-muted-foreground">(default model)</span>
                      )}
                      {m.tool_calling === false && (
                        <span
                          className="ml-2 text-xs text-muted-foreground"
                          title="No tool calling on this provider's API: only an agent without tools can run on it"
                        >
                          (chat-only)
                        </span>
                      )}
                    </td>
                    <td className="hidden sm:table-cell">{fmtPrice(m.pricing_usd_per_mtok.input)}</td>
                    <td className="hidden sm:table-cell">{fmtPrice(m.pricing_usd_per_mtok.output)}</td>
                    <td className="text-right">
                      <input
                        type="checkbox"
                        aria-label={`Enable ${m.model}`}
                        checked={enabled}
                        disabled={!isAdmin || pending || locked}
                        title={
                          !locked
                            ? undefined
                            : isDefault
                              ? "The default model cannot be disabled — change the default model first"
                              : "Tier-assigned models cannot be disabled — reassign the tier first"
                        }
                        onChange={(e) => onToggleModel(m.model, e.target.checked)}
                        className="h-4 w-4 accent-fg-secondary"
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

export function ProvidersPage() {
  const { data: catalog, isLoading, isError, refetch } = useModelsCatalog();
  const { data: providerConfig } = useProviderConfig();
  const updateProvider = useUpdateProviderConfig();

  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const [tierDraft, setTierDraft] = useState<Partial<Record<TierKey, string>>>({});
  const [defaultModelDraft, setDefaultModelDraft] = useState<string | null>(null);

  const disabledModels = useMemo(() => providerConfig?.disabled_models ?? [], [providerConfig]);

  const tierModels = useMemo(
    () => (catalog ? [catalog.heavy, catalog.medium, catalog.light] : []),
    [catalog],
  );

  // Selectable tier options: every enabled model from the catalog, plus the
  // currently-assigned tier values (which may predate the catalog).
  const tierOptions = useMemo(() => {
    const fromCatalog = (catalog?.providers ?? []).flatMap((p) =>
      p.models.filter((m) => m.enabled).map((m) => m.model),
    );
    return [...new Set([...fromCatalog, ...tierModels])];
  }, [catalog, tierModels]);

  const savedDefaultModel = providerConfig?.default_model ?? "";

  // Default-model options: enabled catalog models only — the API refuses an
  // id outside the catalog, and one that is disabled. The saved value is
  // kept in the list so a default that predates that check still shows.
  const defaultModelOptions = useMemo(() => {
    const fromCatalog = (catalog?.providers ?? []).flatMap((p) =>
      p.models.filter((m) => m.enabled).map((m) => m.model),
    );
    return [...new Set([...fromCatalog, ...(savedDefaultModel ? [savedDefaultModel] : [])])];
  }, [catalog, savedDefaultModel]);

  if (isError) {
    return <PageError message="Failed to load provider catalog" onRetry={() => refetch()} />;
  }
  if (isLoading || !catalog) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  const currentTier = (key: TierKey): string => tierDraft[key] ?? tierValue(catalog, key);
  const currentDefaultModel = defaultModelDraft ?? savedDefaultModel;

  const tierChanged =
    TIER_FIELDS.some((t) => {
      const draft = tierDraft[t.key];
      return draft != null && draft !== tierValue(catalog, t.key);
    }) ||
    (defaultModelDraft != null && defaultModelDraft !== savedDefaultModel);

  const handleTierSave = () => {
    const changes: Partial<Record<TierKey | "default_model", string>> = {};
    for (const t of TIER_FIELDS) {
      const draft = tierDraft[t.key];
      if (draft != null && draft !== tierValue(catalog, t.key)) {
        changes[t.key] = draft;
      }
    }
    if (defaultModelDraft != null && defaultModelDraft !== savedDefaultModel) {
      changes.default_model = defaultModelDraft;
    }
    if (Object.keys(changes).length === 0) return;
    updateProvider.mutate(changes, {
      onSuccess: () => {
        setTierDraft({});
        setDefaultModelDraft(null);
        toast.success("Model settings saved");
      },
      onError: (e) => toastApiError(e, "Failed to save model settings"),
    });
  };

  const handleToggleModel = (model: string, enable: boolean) => {
    const next = enable
      ? disabledModels.filter((m) => m !== model)
      : [...new Set([...disabledModels, model])];
    updateProvider.mutate(
      { disabled_models: next },
      {
        onSuccess: () => toast.success(`${model} ${enable ? "enabled" : "disabled"}`),
        onError: (e) => toastApiError(e, `Failed to update ${model}`),
      },
    );
  };

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-xl font-bold tracking-tight">LLM providers</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Every model provider embry0 can run agents on — availability, live
          connection tests, per-model enablement, and tier assignment.
        </p>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg">Model tiers</CardTitle>
            <Button
              size="sm"
              onClick={handleTierSave}
              disabled={!isAdmin || !tierChanged || updateProvider.isPending}
            >
              {updateProvider.isPending ? "Saving..." : "Save tiers"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            The heavy/medium/light defaults agents resolve when no explicit
            model is pinned. Tier-assigned models cannot be disabled.
          </p>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {TIER_FIELDS.map((t) => (
              <div key={t.key}>
                <Label htmlFor={t.key}>{t.label}</Label>
                <Select
                  id={t.key}
                  value={currentTier(t.key)}
                  disabled={!isAdmin}
                  onChange={(e) => setTierDraft((prev) => ({ ...prev, [t.key]: e.target.value }))}
                >
                  {tierOptions.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </Select>
              </div>
            ))}
          </div>
          <div className="mt-4 max-w-xl">
            <Label htmlFor="default_model">Default model</Label>
            <Select
              id="default_model"
              className="max-w-xs"
              value={currentDefaultModel}
              disabled={!isAdmin}
              aria-describedby="default_model_help"
              onChange={(e) => setDefaultModelDraft(e.target.value)}
            >
              <option value="">(none)</option>
              {defaultModelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
            <p id="default_model_help" className="mt-1 text-xs text-muted-foreground">
              When set, every agent — triage, developer, review, QA and the
              rest — runs on this model, whatever its own definition says,
              and triage no longer picks models. A pipeline template step or
              a job that names a model still wins. With (none), each agent
              keeps its own model and triage picks among the tiers above.
            </p>
          </div>
        </CardContent>
      </Card>

      {catalog.providers.map((p) => (
        <ProviderCard
          key={p.name}
          provider={p}
          disabledModels={disabledModels}
          tierModels={tierModels}
          defaultModel={savedDefaultModel}
          isAdmin={isAdmin}
          onToggleModel={handleToggleModel}
          pending={updateProvider.isPending}
        />
      ))}
    </div>
  );
}

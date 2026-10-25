import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Label } from "@/components/ui/Label";
import { Input } from "@/components/ui/Input";
import { LONG_MODEL_LIST, ModelSelect } from "@/components/models/ModelSelect";
import { PageError } from "@/components/PageError";
import { formatDate } from "@/lib/utils";
import { providerLabel, sentenceCase } from "@/lib/status";
import { useIsAdmin } from "@/stores/authStore";
import {
  useModelsCatalog,
  useProviderConfig,
  useRefreshOpenRouterCatalog,
  useTestCatalogProvider,
  useUpdateProviderConfig,
} from "@/hooks/useProvider";
import type { CatalogProvider, ProviderCatalogStatus, ProviderProbeResult } from "@/lib/types/provider";
import { toastApiError } from "@/api/errorToast";
import { ProviderKeyDialog } from "@/components/providers/ProviderKeyDialog";
import { ProviderSpend } from "@/components/providers/ProviderSpend";
import { isLlmProvider } from "@/lib/llmProviders";

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

/** How many rows of a long model list show before "Show all". */
const MODEL_ROWS_SHOWN = 25;

const SKIP_REASON_LABELS: Record<string, string> = {
  no_tools: "no tool calling",
  batch: "batch",
  free: "free (rate-limited)",
  online: "web search",
  variable_price: "price set per request",
  unpriced: "no price",
  malformed: "unreadable",
};

/** Where OpenRouter's model list comes from, when it was read, and a button to read it now. */
function CatalogStatus({ status, isAdmin }: { status: ProviderCatalogStatus; isAdmin: boolean }) {
  const refresh = useRefreshOpenRouterCatalog();
  const skipped = Object.entries(status.skipped ?? {})
    .filter(([, n]) => n > 0)
    .map(([reason, n]) => `${n} ${SKIP_REASON_LABELS[reason] ?? reason}`)
    .join(", ");
  return (
    <div className="space-y-1" data-testid="provider-catalog">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>
          Models:{" "}
          {status.source === "live" ? (
            <>
              OpenRouter&apos;s live list, {status.models ?? 0} with tool calling, read{" "}
              {status.fetched_at ? <time dateTime={status.fetched_at}>{formatDate(status.fetched_at)}</time> : "—"}
              {status.refresh_hours ? `, every ${status.refresh_hours} h` : ""}.
            </>
          ) : (
            "the built-in entry, until OpenRouter's list is read."
          )}
        </span>
        {isAdmin && (
          <Button
            variant="outline"
            size="sm"
            disabled={refresh.isPending}
            onClick={() =>
              refresh.mutate(undefined, {
                onSuccess: (next) =>
                  next.last_error
                    ? toast.error(`OpenRouter's list was not read: ${next.last_error}`)
                    : toast.success(`OpenRouter's list read: ${next.models ?? 0} models`),
                onError: (e) => toastApiError(e, "Failed to read OpenRouter's list"),
              })
            }
          >
            {refresh.isPending ? "Reading..." : "Read the list now"}
          </Button>
        )}
      </div>
      {skipped && <p className="text-xs text-muted-foreground">Left out: {skipped}.</p>}
      {status.last_error && (
        <p className="text-xs text-warning" data-testid="provider-catalog-error">
          The last read failed
          {status.last_error_at ? (
            <>
              {" "}
              (<time dateTime={status.last_error_at}>{formatDate(status.last_error_at)}</time>)
            </>
          ) : null}
          : {status.last_error}. The list in force stays.
        </p>
      )}
    </div>
  );
}

function ProviderCard({
  provider,
  disabledModels,
  tierModels,
  defaultModel,
  fallbackModels,
  isAdmin,
  onToggleModel,
  pending,
}: {
  provider: CatalogProvider;
  disabledModels: string[];
  tierModels: string[];
  defaultModel: string;
  fallbackModels: string[];
  isAdmin: boolean;
  onToggleModel: (model: string, enable: boolean) => void;
  pending: boolean;
}) {
  const testProvider = useTestCatalogProvider();
  const [probe, setProbe] = useState<ProviderProbeResult | null>(null);
  const [modelFilter, setModelFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const longList = provider.models.length > LONG_MODEL_LIST;
  const needle = modelFilter.trim().toLowerCase();
  const matching = needle
    ? provider.models.filter((m) => m.model.toLowerCase().includes(needle) || (m.name ?? "").toLowerCase().includes(needle))
    : provider.models;
  const rows = longList && !needle && !showAll ? matching.slice(0, MODEL_ROWS_SHOWN) : matching;
  const [keyDialog, setKeyDialog] = useState(false);
  // A key entered here: every LLM provider but xAI's OAuth, which logs in on the Credentials page.
  const takesKey = isAdmin && isLlmProvider(provider.name);

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
          <div className="flex gap-2">
            {takesKey && (
              <Button variant="outline" size="sm" onClick={() => setKeyDialog(true)}>
                {provider.credential_source ? "Replace key" : "Add key"}
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={handleTest} disabled={testProvider.isPending}>
              {testProvider.isPending ? "Testing..." : "Test connection"}
            </Button>
          </div>
        </div>
        {keyDialog && isLlmProvider(provider.name) && (
          <ProviderKeyDialog
            provider={provider.name}
            replacing={Boolean(provider.credential_source)}
            onClose={() => setKeyDialog(false)}
          />
        )}
        {/* A URL has no spaces to wrap at; a long one ran past a phone screen. */}
        <p className="text-xs text-muted-foreground font-mono [overflow-wrap:anywhere]">{provider.base_url}</p>
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
        <ProviderSpend provider={provider.name} />
        {provider.catalog && <CatalogStatus status={provider.catalog} isAdmin={isAdmin} />}
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

        {longList && (
          <Input
            type="search"
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            placeholder={`Filter ${provider.models.length} models`}
            aria-label={`Filter ${providerLabel(provider.name)} models`}
            className="mb-3 h-8 max-w-xs text-xs"
          />
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
              {rows.map((m) => {
                const isTier = tierModels.includes(m.model);
                const isDefault = defaultModel !== "" && m.model === defaultModel;
                const isFallback = fallbackModels.includes(m.model);
                const enabled = !disabledModels.includes(m.model);
                // Backend interlock: a model that a tier, the default model or
                // the fallback order points at cannot be disabled — change that first.
                const locked = (isTier || isDefault || isFallback) && enabled;
                return (
                  <tr key={m.model}>
                    <td className="font-mono" title={m.name}>
                      {m.model}
                      {isTier && (
                        <span className="ml-2 text-xs text-muted-foreground">(tier-assigned)</span>
                      )}
                      {isDefault && (
                        <span className="ml-2 text-xs text-muted-foreground">(default model)</span>
                      )}
                      {isFallback && (
                        <span className="ml-2 text-xs text-muted-foreground">(fallback)</span>
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
                              : isTier
                                ? "Tier-assigned models cannot be disabled — reassign the tier first"
                                : "A fallback model cannot be disabled — remove it from the fallback order first"
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
        {longList && rows.length < matching.length && (
          <Button variant="outline" size="sm" className="mt-3" onClick={() => setShowAll(true)}>
            Show all {matching.length} models
          </Button>
        )}
        {longList && needle && matching.length === 0 && (
          <p className="mt-3 text-xs text-muted-foreground">No model matches “{modelFilter.trim()}”.</p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Provider fallback: the models, in order, an agent runs on when the account
 * its own model needs is paused (a spent usage window, a rate limit) or
 * missing. Empty is off.
 */
function FallbackOrderCard({
  saved,
  options,
  providers,
  isAdmin,
  pending,
  onSave,
}: {
  saved: string[];
  options: string[];
  providers: CatalogProvider[];
  isAdmin: boolean;
  pending: boolean;
  onSave: (order: string[], done: () => void) => void;
}) {
  const [draft, setDraft] = useState<string[] | null>(null);
  const [adding, setAdding] = useState("");
  const order = draft ?? saved;
  const addable = options.filter((m) => !order.includes(m));
  const changed = draft != null && draft.join("\n") !== saved.join("\n");

  const move = (index: number, by: number) => {
    const next = [...order];
    const [model] = next.splice(index, 1);
    next.splice(index + by, 0, model);
    setDraft(next);
  };

  return (
    <Card data-testid="fallback-order">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Fallback order</CardTitle>
          <Button
            size="sm"
            onClick={() => onSave(order, () => setDraft(null))}
            disabled={!isAdmin || !changed || pending}
          >
            {pending ? "Saving..." : "Save order"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          When the account an agent&apos;s model needs is paused (out of usage, rate-limited) or missing, the
          agent runs on the first model below whose provider has a usable account, and the job says so. A model
          the job names itself, or a key chosen with Run with, is never replaced. Empty: off, and a paused
          account fails the job.
        </p>
      </CardHeader>
      <CardContent>
        {order.length === 0 ? (
          <p className="text-sm text-muted-foreground">No fallback: off.</p>
        ) : (
          <ol className="space-y-1">
            {order.map((model, i) => (
              <li key={model} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="w-5 text-muted-foreground">{i + 1}.</span>
                <span className="font-mono">{model}</span>
                {isAdmin && (
                  <span className="ml-auto flex gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Move ${model} up`}
                      disabled={i === 0}
                      onClick={() => move(i, -1)}
                    >
                      ↑
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Move ${model} down`}
                      disabled={i === order.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      ↓
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`Remove ${model}`}
                      onClick={() => setDraft(order.filter((m) => m !== model))}
                    >
                      Remove
                    </Button>
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
        {isAdmin && addable.length > 0 && (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <div>
              <Label htmlFor="fallback_add">Add a model</Label>
              <ModelSelect
                id="fallback_add"
                className="max-w-xs"
                value={adding}
                onChange={(e) => setAdding(e.target.value)}
                models={addable}
                providers={providers}
                filterLabel="Filter fallback models"
                leading={<option value="">(choose)</option>}
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!adding}
              onClick={() => {
                setDraft([...order, adding]);
                setAdding("");
              }}
            >
              Add
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ProvidersPage() {
  const { data: catalog, isLoading, isError, refetch } = useModelsCatalog();
  const { data: providerConfig } = useProviderConfig();
  const updateProvider = useUpdateProviderConfig();

  const isAdmin = useIsAdmin();

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
  const fallbackModels = useMemo(() => providerConfig?.fallback_models ?? [], [providerConfig]);

  // Fallback options: enabled catalog models that call tools — a fallback
  // replaces the model of agents that use them, and the API refuses the rest.
  const fallbackOptions = useMemo(
    () =>
      (catalog?.providers ?? []).flatMap((p) =>
        p.models.filter((m) => m.enabled && m.tool_calling !== false).map((m) => m.model),
      ),
    [catalog],
  );

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
        <h1 className="title-pixel text-xl font-bold tracking-tight">LLM providers</h1>
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
                <ModelSelect
                  id={t.key}
                  value={currentTier(t.key)}
                  disabled={!isAdmin}
                  onChange={(e) => setTierDraft((prev) => ({ ...prev, [t.key]: e.target.value }))}
                  models={tierOptions}
                  providers={catalog.providers}
                  filterLabel={`Filter ${t.label.toLowerCase()} tier models`}
                />
              </div>
            ))}
          </div>
          <div className="mt-4 max-w-xl">
            <Label htmlFor="default_model">Default model</Label>
            <ModelSelect
              id="default_model"
              className="max-w-xs"
              value={currentDefaultModel}
              disabled={!isAdmin}
              aria-describedby="default_model_help"
              onChange={(e) => setDefaultModelDraft(e.target.value)}
              models={defaultModelOptions}
              providers={catalog.providers}
              filterLabel="Filter default models"
              leading={<option value="">(none)</option>}
            />
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

      {providerConfig?.fallback_models !== undefined && (
        <FallbackOrderCard
          saved={fallbackModels}
          options={fallbackOptions}
          providers={catalog.providers}
          isAdmin={isAdmin}
          pending={updateProvider.isPending}
          onSave={(order, done) =>
            updateProvider.mutate(
              { fallback_models: order },
              {
                onSuccess: () => {
                  done();
                  toast.success(order.length ? "Fallback order saved" : "Fallback turned off");
                },
                onError: (e) => toastApiError(e, "Failed to save the fallback order"),
              },
            )
          }
        />
      )}

      {catalog.providers.map((p) => (
        <ProviderCard
          key={p.name}
          provider={p}
          disabledModels={disabledModels}
          tierModels={tierModels}
          defaultModel={savedDefaultModel}
          fallbackModels={fallbackModels}
          isAdmin={isAdmin}
          onToggleModel={handleToggleModel}
          pending={updateProvider.isPending}
        />
      ))}
    </div>
  );
}

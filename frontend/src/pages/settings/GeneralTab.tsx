import { useState, useEffect, useMemo } from "react";
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { useProviderConfig, useUpdateProviderConfig, useTestProviderConnection } from "@/hooks/useProvider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { ConnectionStatus } from "@/components/ui/ConnectionStatus";
import { PageError } from "@/components/PageError";
import { toast } from "sonner";
import type { ConfigResponse } from "@/lib/types";
import type { ProviderConfig, ProviderConfigUpdate, ProviderMode } from "@/lib/types/provider";

function parseNumber(value: string, fallback: number): number {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isNaN(n) ? fallback : n;
}

export default function GeneralTab() {
  const { data: config, isLoading: configLoading, isError: configError, refetch: refetchConfig } = useConfig();
  const updateConfig = useUpdateConfig();

  const { data: providerConfig, isLoading: providerLoading, isError: providerError, refetch: refetchProvider } = useProviderConfig();
  const updateProvider = useUpdateProviderConfig();
  const testConnection = useTestProviderConnection();

  const [form, setForm] = useState<ConfigResponse | null>(null);
  const [providerForm, setProviderForm] = useState<ProviderConfig | null>(null);

  // Sync budget form from config on initial load
  useEffect(() => {
    if (config) setForm((prev) => prev ?? { ...config });
  }, [config]);

  // Sync provider form from config on initial load
  useEffect(() => {
    if (providerConfig) setProviderForm((prev) => prev ?? { ...providerConfig });
  }, [providerConfig]);

  // Track unsaved changes for budget
  const hasBudgetChanges = useMemo(() => {
    if (!form || !config) return false;
    return (Object.keys(form) as (keyof ConfigResponse)[]).some(
      (key) => form[key] !== config[key],
    );
  }, [form, config]);

  // Track unsaved changes for provider
  const hasProviderChanges = useMemo(() => {
    if (!providerForm || !providerConfig) return false;
    return (
      providerForm.provider_mode !== providerConfig.provider_mode ||
      providerForm.model_heavy !== providerConfig.model_heavy ||
      providerForm.model_medium !== providerConfig.model_medium ||
      providerForm.model_light !== providerConfig.model_light ||
      providerForm.default_model !== providerConfig.default_model ||
      providerForm.ollama_base_url !== providerConfig.ollama_base_url
    );
  }, [providerForm, providerConfig]);

  const hasChanges = hasBudgetChanges || hasProviderChanges;

  // Warn on page unload if unsaved changes
  useEffect(() => {
    if (!hasChanges) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasChanges]);

  if (configError || providerError) {
    return (
      <PageError
        message="Failed to load settings"
        onRetry={() => { refetchConfig(); refetchProvider(); }}
      />
    );
  }

  if (configLoading || providerLoading || !form || !providerForm) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  const handleChange = (field: keyof ConfigResponse, value: string | number | boolean) => {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const handleNumberChange = (field: keyof ConfigResponse, value: string, min?: number, max?: number) => {
    const fallback = (config?.[field] as number) ?? 0;
    let n = parseNumber(value, fallback);
    if (min != null && n < min) n = min;
    if (max != null && n > max) n = max;
    handleChange(field, n);
  };

  const handleProviderChange = <K extends keyof ProviderConfigUpdate>(field: K, value: ProviderConfigUpdate[K]) => {
    setProviderForm((prev) => (prev ? { ...prev, [field]: value } : prev));
  };

  const handleSave = async () => {
    const promises: Promise<unknown>[] = [];

    // Save budget changes
    if (hasBudgetChanges && form && config) {
      const changes: Record<string, unknown> = {};
      for (const key of Object.keys(form) as (keyof ConfigResponse)[]) {
        if (form[key] !== config[key]) {
          changes[key] = form[key];
        }
      }
      if (Object.keys(changes).length > 0) {
        promises.push(
          new Promise((resolve, reject) => {
            updateConfig.mutate(changes, {
              onSuccess: () => {
                setForm(null);
                resolve(undefined);
              },
              onError: (e) => reject(e),
            });
          }),
        );
      }
    }

    // Save provider changes
    if (hasProviderChanges && providerForm && providerConfig) {
      const providerChanges: ProviderConfigUpdate = {};
      if (providerForm.provider_mode !== providerConfig.provider_mode) providerChanges.provider_mode = providerForm.provider_mode;
      if (providerForm.model_heavy !== providerConfig.model_heavy) providerChanges.model_heavy = providerForm.model_heavy;
      if (providerForm.model_medium !== providerConfig.model_medium) providerChanges.model_medium = providerForm.model_medium;
      if (providerForm.model_light !== providerConfig.model_light) providerChanges.model_light = providerForm.model_light;
      if (providerForm.default_model !== providerConfig.default_model) providerChanges.default_model = providerForm.default_model;
      if (providerForm.ollama_base_url !== providerConfig.ollama_base_url) providerChanges.ollama_base_url = providerForm.ollama_base_url;

      if (Object.keys(providerChanges).length > 0) {
        promises.push(
          new Promise((resolve, reject) => {
            updateProvider.mutate(providerChanges, {
              onSuccess: () => {
                setProviderForm(null);
                resolve(undefined);
              },
              onError: (e) => reject(e),
            });
          }),
        );
      }
    }

    if (promises.length === 0) {
      toast.info("No changes to save");
      return;
    }

    try {
      await Promise.all(promises);
      toast.success("Settings saved");
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    }
  };

  const handleReset = () => {
    if (config) setForm({ ...config });
    if (providerConfig) setProviderForm({ ...providerConfig });
  };

  const handleTestConnection = () => {
    testConnection.mutate(undefined, {
      onSuccess: (result) => {
        if (result.status === "ok") {
          toast.success(result.message || "Connection successful");
        } else {
          toast.error(result.message || "Connection failed");
        }
      },
      onError: (e) => toast.error(`Connection test failed: ${e.message}`),
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div />
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} disabled={!hasChanges}>
            Reset
          </Button>
          <Button onClick={handleSave} disabled={updateConfig.isPending || updateProvider.isPending || !hasChanges}>
            {updateConfig.isPending || updateProvider.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}

      {/* Budget & Limits */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Budget & Limits</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="max_budget_per_job_usd">Max Budget per Job (USD)</Label>
              <Input
                id="max_budget_per_job_usd"
                type="number"
                step="0.01"
                min="0.01"
                max="1000"
                value={form.max_budget_per_job_usd as number}
                onChange={(e) => handleNumberChange("max_budget_per_job_usd", e.target.value, 0.01, 1000)}
              />
            </div>
            <div>
              <Label htmlFor="daily_cap_usd">Daily Budget Cap (USD)</Label>
              <Input
                id="daily_cap_usd"
                type="number"
                step="1"
                min="0"
                max="10000"
                value={form.daily_cap_usd as number}
                onChange={(e) => handleNumberChange("daily_cap_usd", e.target.value, 0, 10000)}
              />
            </div>
            <div>
              <Label htmlFor="monthly_cap_usd">Monthly Budget Cap (USD)</Label>
              <Input
                id="monthly_cap_usd"
                type="number"
                step="1"
                min="0"
                max="100000"
                value={form.monthly_cap_usd as number}
                onChange={(e) => handleNumberChange("monthly_cap_usd", e.target.value, 0, 100000)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Rate Limiting */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Rate Limiting</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="rate_limit_per_author_per_hour">Rate Limit (per author/hour)</Label>
              <Input
                id="rate_limit_per_author_per_hour"
                type="number"
                min="1"
                max="100"
                value={form.rate_limit_per_author_per_hour as number}
                onChange={(e) => handleNumberChange("rate_limit_per_author_per_hour", e.target.value, 1, 100)}
              />
            </div>
            <div>
              <Label htmlFor="overrun_mode">Overrun Mode</Label>
              <Select
                id="overrun_mode"
                value={form.overrun_mode as string}
                onChange={(e) => handleChange("overrun_mode", e.target.value)}
              >
                <option value="soft">Soft (warn, continue)</option>
                <option value="hard">Hard (stop at cap)</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Provider Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Provider Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="provider_mode">Provider Mode</Label>
                <Select
                  id="provider_mode"
                  value={providerForm.provider_mode}
                  onChange={(e) => handleProviderChange("provider_mode", e.target.value as ProviderMode)}
                >
                  <option value="anthropic_api">Anthropic API</option>
                  <option value="claude_max">Claude Max</option>
                  <option value="ollama">Ollama</option>
                </Select>
              </div>
              <div>
                <Label htmlFor="default_model">Default Model Override</Label>
                <Input
                  id="default_model"
                  value={providerForm.default_model}
                  onChange={(e) => handleProviderChange("default_model", e.target.value)}
                  placeholder="Optional override"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <Label htmlFor="model_heavy">Heavy Tier Model</Label>
                <Input
                  id="model_heavy"
                  value={providerForm.model_heavy}
                  onChange={(e) => handleProviderChange("model_heavy", e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="model_medium">Medium Tier Model</Label>
                <Input
                  id="model_medium"
                  value={providerForm.model_medium}
                  onChange={(e) => handleProviderChange("model_medium", e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="model_light">Light Tier Model</Label>
                <Input
                  id="model_light"
                  value={providerForm.model_light}
                  onChange={(e) => handleProviderChange("model_light", e.target.value)}
                />
              </div>
            </div>

            {providerForm.provider_mode === "ollama" && (
              <div>
                <Label htmlFor="ollama_base_url">Ollama Base URL</Label>
                <Input
                  id="ollama_base_url"
                  value={providerForm.ollama_base_url}
                  onChange={(e) => handleProviderChange("ollama_base_url", e.target.value)}
                  placeholder="http://localhost:11434"
                />
              </div>
            )}

            <div className="flex items-center justify-between pt-2">
              <div className="flex gap-4">
                <ConnectionStatus configured={providerConfig!.api_key_set} label="API Key" />
                <ConnectionStatus configured={providerConfig!.oauth_token_set} label="OAuth Token" />
              </div>
              <Button
                variant="outline"
                onClick={handleTestConnection}
                disabled={testConnection.isPending}
              >
                {testConnection.isPending ? "Testing..." : "Test Connection"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

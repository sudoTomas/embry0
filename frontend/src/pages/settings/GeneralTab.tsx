import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { useLayoutStore } from "@/stores/layoutStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { PageError } from "@/components/PageError";
import { clearStoredApiKey } from "@/lib/apiKey";
import { useAuthStore } from "@/stores/authStore";
import { toast } from "sonner";
import type { ConfigResponse } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";

type DensityMode = "comfortable" | "standard" | "compact";

function parseNumber(value: string, fallback: number): number {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isNaN(n) ? fallback : n;
}

export default function GeneralTab() {
  const { data: config, isLoading: configLoading, isError: configError, refetch: refetchConfig } = useConfig();
  const updateConfig = useUpdateConfig();
  const navigate = useNavigate();

  const densityMode = useLayoutStore((s) => s.densityMode);
  const setDensityMode = useLayoutStore((s) => s.setDensityMode);

  const [form, setForm] = useState<ConfigResponse | null>(null);

  // Sync budget form from config on initial load
  useEffect(() => {
    if (config) setForm((prev) => prev ?? { ...config });
  }, [config]);

  // Track unsaved changes for budget
  const hasChanges = useMemo(() => {
    if (!form || !config) return false;
    return (Object.keys(form) as (keyof ConfigResponse)[]).some(
      (key) => form[key] !== config[key],
    );
  }, [form, config]);

  // Warn on page unload if unsaved changes
  useEffect(() => {
    if (!hasChanges) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasChanges]);

  if (configError) {
    return (
      <PageError
        message="Failed to load settings"
        onRetry={() => { refetchConfig(); }}
      />
    );
  }

  if (configLoading || !form) {
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

  const handleSave = () => {
    if (!hasChanges || !form || !config) {
      toast.info("No changes to save");
      return;
    }
    const changes: Record<string, unknown> = {};
    for (const key of Object.keys(form) as (keyof ConfigResponse)[]) {
      if (form[key] !== config[key]) {
        changes[key] = form[key];
      }
    }
    updateConfig.mutate(changes, {
      onSuccess: () => {
        setForm(null);
        toast.success("Settings saved");
      },
      onError: (e) => toastApiError(e, "Failed"),
    });
  };

  const handleReset = () => {
    if (config) setForm({ ...config });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div />
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} disabled={!hasChanges}>
            Reset
          </Button>
          <Button onClick={handleSave} disabled={updateConfig.isPending || !hasChanges}>
            {updateConfig.isPending ? "Saving..." : "Save changes"}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}

      {/* Budget & Limits */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Budget & limits</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="max_budget_per_job_usd">Max budget per job (USD)</Label>
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
              <Label htmlFor="daily_cap_usd">Daily budget cap (USD)</Label>
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
              <Label htmlFor="monthly_cap_usd">Monthly budget cap (USD)</Label>
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
          <CardTitle className="text-lg">Rate limiting</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="rate_limit_per_author_per_hour">Rate limit (per author/hour)</Label>
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
              <Label htmlFor="overrun_mode">Overrun mode</Label>
              <Select
                id="overrun_mode"
                value={form.overrun_mode as string}
                onChange={(e) => handleChange("overrun_mode", e.target.value)}
              >
                <option value="soft">Soft (finish the node, then pause)</option>
                <option value="hard">Hard (fail the job at the cap)</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Display */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Display</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="density_mode">Density</Label>
              <Select
                id="density_mode"
                value={densityMode}
                onChange={(e) => setDensityMode(e.target.value as DensityMode)}
              >
                <option value="comfortable">Comfortable (default)</option>
                <option value="standard">Standard</option>
                <option value="compact">Compact</option>
              </Select>
              <p className="text-xs text-muted-foreground mt-1.5">
                Saved locally; takes effect immediately, persists across reloads.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* RAV-1297: model tiers, per-model enablement, credential status, and
          connection tests moved to the dedicated LLM Providers page. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">LLM providers</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              Model tiers, per-model enablement, credential status, and
              connection tests live on the LLM Providers page.
            </p>
            <Button variant="outline" onClick={() => navigate("/providers")}>
              Open LLM Providers
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Dashboard Access — the browser-stored API key (RAV-1058) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Dashboard access</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              Your API key is stored only in this browser. Sign out to clear it
              or switch to a different key.
            </p>
            <Button
              variant="outline"
              onClick={() => {
                clearStoredApiKey();
                useAuthStore.getState().requirePrompt({ sessionExpired: false });
              }}
            >
              Sign out
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

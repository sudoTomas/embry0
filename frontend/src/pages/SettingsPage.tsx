import { useState, useEffect, useMemo } from "react";
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { PageError } from "@/components/PageError";
import { toast } from "sonner";
import type { ConfigResponse } from "@/lib/types";

function parseNumber(value: string, fallback: number): number {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isNaN(n) ? fallback : n;
}

export function SettingsPage() {
  const { data: config, isLoading, isError, refetch } = useConfig();
  const updateConfig = useUpdateConfig();
  const [form, setForm] = useState<ConfigResponse | null>(null);

  // Only sync form from config on initial load or refetch -- NOT dependent on form
  useEffect(() => {
    if (config) setForm((prev) => prev ?? { ...config });
  }, [config]);

  // Track unsaved changes
  const hasChanges = useMemo(() => {
    if (!form || !config) return false;
    return (Object.keys(form) as (keyof ConfigResponse)[]).some(
      (key) => form[key] !== config[key]
    );
  }, [form, config]);

  // Warn on page unload if there are unsaved changes
  useEffect(() => {
    if (!hasChanges) return;

    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasChanges]);

  if (isError) {
    return <PageError message="Failed to load settings" onRetry={() => refetch()} />;
  }

  if (isLoading || !form) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  const handleChange = (field: keyof ConfigResponse, value: string | number | boolean) => {
    setForm((prev) => prev ? { ...prev, [field]: value } : prev);
  };

  const handleNumberChange = (field: keyof ConfigResponse, value: string, min?: number, max?: number) => {
    const fallback = (config?.[field] as number) ?? 0;
    let n = parseNumber(value, fallback);
    if (min != null && n < min) n = min;
    if (max != null && n > max) n = max;
    handleChange(field, n);
  };

  const handleSave = () => {
    if (!form || !config) return;
    const changes: Record<string, unknown> = {};
    for (const key of Object.keys(form) as (keyof ConfigResponse)[]) {
      if (form[key] !== config[key]) {
        changes[key] = form[key];
      }
    }
    if (Object.keys(changes).length === 0) {
      toast.info("No changes to save");
      return;
    }
    updateConfig.mutate(changes, {
      onSuccess: () => {
        setForm(null);
        toast.success("Settings saved");
      },
      onError: (e) => toast.error(`Failed: ${e.message}`),
    });
  };

  const handleReset = () => {
    if (config) setForm({ ...config });
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Settings</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} disabled={!hasChanges}>
            Reset
          </Button>
          <Button onClick={handleSave} disabled={updateConfig.isPending || !hasChanges}>
            {updateConfig.isPending ? "Saving..." : "Save Changes"}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Budget & Limits</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="max_budget_per_job_usd">Max Budget per Job (USD)</Label>
              <Input id="max_budget_per_job_usd" type="number" step="0.01" min="0.01" max="1000"
                value={form.max_budget_per_job_usd as number}
                onChange={(e) => handleNumberChange("max_budget_per_job_usd", e.target.value, 0.01, 1000)} />
            </div>
            <div>
              <Label htmlFor="daily_cap_usd">Daily Budget Cap (USD)</Label>
              <Input id="daily_cap_usd" type="number" step="1" min="0" max="10000"
                value={form.daily_cap_usd as number}
                onChange={(e) => handleNumberChange("daily_cap_usd", e.target.value, 0, 10000)} />
            </div>
            <div>
              <Label htmlFor="monthly_cap_usd">Monthly Budget Cap (USD)</Label>
              <Input id="monthly_cap_usd" type="number" step="1" min="0" max="100000"
                value={form.monthly_cap_usd as number}
                onChange={(e) => handleNumberChange("monthly_cap_usd", e.target.value, 0, 100000)} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Rate Limiting</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="rate_limit_per_author_per_hour">Rate Limit (per author/hour)</Label>
              <Input id="rate_limit_per_author_per_hour" type="number" min="1" max="100"
                value={form.rate_limit_per_author_per_hour as number}
                onChange={(e) => handleNumberChange("rate_limit_per_author_per_hour", e.target.value, 1, 100)} />
            </div>
            <div>
              <Label htmlFor="overrun_mode">Overrun Mode</Label>
              <Select id="overrun_mode" value={form.overrun_mode as string}
                onChange={(e) => handleChange("overrun_mode", e.target.value)}>
                <option value="soft">Soft (warn, continue)</option>
                <option value="hard">Hard (stop at cap)</option>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Provider Configuration */}
      <div className="legion-card p-6">
        <h2 className="text-lg font-bold mb-4">Provider</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label>Mode</Label>
            <div className="text-sm text-white/60 mt-1 font-mono">
              {(config as Record<string, unknown>)?.provider_mode as string ?? "anthropic_api"}
            </div>
          </div>
          <div>
            <Label>Default Model</Label>
            <div className="text-sm text-white/60 mt-1 font-mono">
              {(config as Record<string, unknown>)?.default_model as string ?? "claude-sonnet-4-6"}
            </div>
          </div>
        </div>
        <p className="text-xs text-white/30 mt-3">
          Provider settings are configured via environment variables. Restart the stack to apply changes.
        </p>
      </div>

      {/* Context Injection */}
      <div className="legion-card p-6">
        <h2 className="text-lg font-bold mb-4">Context Injection</h2>
        <div className="space-y-4">
          <div>
            <Label>Global System Context</Label>
            <Textarea
              placeholder="Coding standards, architectural guidelines, team conventions..."
              className="mt-1 min-h-[80px] bg-[#0c1015] border-white/[0.08]"
            />
          </div>
          <div>
            <Label>Global Assistant Context</Label>
            <Textarea
              placeholder="Issue-specific instructions, prior context..."
              className="mt-1 min-h-[80px] bg-[#0c1015] border-white/[0.08]"
            />
          </div>
        </div>
        <p className="text-xs text-white/30 mt-3">
          Context is prepended to agent prompts. Per-repo context can be configured via the API.
        </p>
      </div>
    </div>
  );
}

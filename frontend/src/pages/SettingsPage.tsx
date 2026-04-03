import { useState, useEffect, useMemo } from "react";
import { useConfig, useUpdateConfig } from "@/hooks/useConfig";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { PageError } from "@/components/PageError";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { TriageForm } from "@/components/triage/TriageForm";
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

  const configurationContent = (
    <div className="space-y-6 mt-4">
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
              <Label htmlFor="max_budget_usd">Max Budget per Job (USD)</Label>
              <Input id="max_budget_usd" type="number" step="0.01" min="0.01" max="1000"
                value={form.max_budget_usd}
                onChange={(e) => handleNumberChange("max_budget_usd", e.target.value, 0.01, 1000)} />
            </div>
            <div>
              <Label htmlFor="daily_budget_cap_usd">Daily Budget Cap (USD)</Label>
              <Input id="daily_budget_cap_usd" type="number" step="1" min="0" max="10000"
                value={form.daily_budget_cap_usd}
                onChange={(e) => handleNumberChange("daily_budget_cap_usd", e.target.value, 0, 10000)} />
            </div>
            <div>
              <Label htmlFor="monthly_budget_cap_usd">Monthly Budget Cap (USD)</Label>
              <Input id="monthly_budget_cap_usd" type="number" step="1" min="0" max="100000"
                value={form.monthly_budget_cap_usd}
                onChange={(e) => handleNumberChange("monthly_budget_cap_usd", e.target.value, 0, 100000)} />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Agent Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="provider_mode">Provider Mode</Label>
              <Select id="provider_mode" value={form.provider_mode}
                onChange={(e) => handleChange("provider_mode", e.target.value)}>
                <option value="anthropic_api">Anthropic API</option>
                <option value="claude_max">Claude Max</option>
                <option value="ollama">Ollama</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="default_model">Default Model</Label>
              <Input id="default_model" value={form.default_model}
                onChange={(e) => handleChange("default_model", e.target.value)} />
            </div>
            <div>
              <Label htmlFor="sandbox_memory">Sandbox Memory</Label>
              <Input id="sandbox_memory" value={form.sandbox_memory}
                onChange={(e) => handleChange("sandbox_memory", e.target.value)}
                placeholder="e.g. 8g" />
            </div>
            <div>
              <Label htmlFor="sandbox_cpus">Sandbox CPUs</Label>
              <Input id="sandbox_cpus" value={form.sandbox_cpus}
                onChange={(e) => handleChange("sandbox_cpus", e.target.value)}
                placeholder="e.g. 4" />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Pipeline Behavior</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="trigger_labels">Trigger Labels (comma-separated)</Label>
              <Input id="trigger_labels" value={form.trigger_labels}
                onChange={(e) => handleChange("trigger_labels", e.target.value)}
                placeholder="Legion" />
            </div>
            <div>
              <Label htmlFor="rate_limit">Rate Limit (per author/hour)</Label>
              <Input id="rate_limit" type="number" min="1" max="100"
                value={form.rate_limit_per_author_per_hour}
                onChange={(e) => handleNumberChange("rate_limit_per_author_per_hour", e.target.value, 1, 100)} />
            </div>
<div className="flex items-center gap-3 pt-6">
              <input type="checkbox" id="clone_repos"
                checked={form.clone_repos_per_issue}
                onChange={(e) => handleChange("clone_repos_per_issue", e.target.checked)}
                className="h-4 w-4 rounded border-input" />
              <Label htmlFor="clone_repos">Clone repo per issue (isolation)</Label>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Notifications</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <Label htmlFor="slack_url">Slack Webhook URL</Label>
              <Input id="slack_url" type="password" value={form.slack_webhook_url}
                onChange={(e) => handleChange("slack_webhook_url", e.target.value)}
                placeholder="https://hooks.slack.com/services/..." />
            </div>
            <div>
              <Label htmlFor="telegram_token">Telegram Bot Token</Label>
              <Input id="telegram_token" type="password" value={form.telegram_bot_token}
                onChange={(e) => handleChange("telegram_bot_token", e.target.value)} />
            </div>
            <div>
              <Label htmlFor="telegram_chat">Telegram Chat ID</Label>
              <Input id="telegram_chat" value={form.telegram_chat_id}
                onChange={(e) => handleChange("telegram_chat_id", e.target.value)} />
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );

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

      <Tabs defaultValue="configuration">
        <TabsList>
          <TabsTrigger value="configuration">Configuration</TabsTrigger>
          <TabsTrigger value="triage">Triage</TabsTrigger>
        </TabsList>
        <TabsContent value="configuration">
          {configurationContent}
        </TabsContent>
        <TabsContent value="triage">
          <div className="space-y-6 mt-4">
            <p className="text-muted-foreground">Test the complexity triage engine with a sample issue.</p>
            <TriageForm />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

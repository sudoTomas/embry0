import { useState, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import {
  useBudgetSettings,
  useSetBudgetLimits,
  BUDGET_SECTION,
  type BudgetProvenance,
} from "@/hooks/useBudgetSettings";
import { Badge } from "@/components/ui/Badge";
import { actorInText } from "@/lib/actors";
import { formatDateTime } from "@/lib/utils";
import type { EffectiveSetting } from "@/lib/types/settings";
import { useSettingsWriteAccess } from "@/hooks/useSettingsDocument";
import { documentProblems, notImportedText } from "@/lib/settingsDocument";
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
import type { BudgetLimits } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";

type DensityMode = "comfortable" | "standard" | "compact";

/** Why a field was refused: the 422's message for `budget.<field>`. */
type FieldProblems = Partial<Record<keyof BudgetLimits, string>>;

function FieldProblem({ field, problems }: { field: keyof BudgetLimits; problems: FieldProblems }) {
  const message = problems[field];
  if (!message) return null;
  return (
    <p id={`${field}-problem`} role="alert" className="mt-1 text-xs text-destructive">
      {message}
    </p>
  );
}

/**
 * Where a limit's value comes from: its default, the import at upgrade, or a person's change, and who and
 * when. Until the import succeeds, a limit shows the value enforced meanwhile and where it is read from.
 */
function provenanceText(setting: EffectiveSetting): string {
  if (setting.source === "default") return "Default";
  if (setting.source === "not_imported") return notImportedText(setting.not_imported);
  const where = setting.source === "bootstrap" ? "Imported at upgrade" : "Set";
  const when = setting.set_at ? ` on ${formatDateTime(setting.set_at)}` : "";
  // Changed before the kept history: who and when are not known.
  if (setting.source === "bootstrap" || (!setting.set_by && !setting.set_at)) return `${where}${when}`;
  return `Set by ${actorInText(setting.set_by)}${when}`;
}

function Provenance({ field, provenance }: { field: keyof BudgetLimits; provenance: BudgetProvenance }) {
  const setting = provenance[field];
  if (!setting) return null;
  return (
    <p className="mt-1 text-xs" data-testid={`${field}-source`}>
      <Badge tone={setting.source === "default" ? "neutral" : setting.source === "not_imported" ? "warning" : "info"}>
        {provenanceText(setting)}
      </Badge>
    </p>
  );
}

function parseNumber(value: string, fallback: number): number {
  if (value.trim() === "") return fallback;
  const n = Number(value);
  return Number.isNaN(n) ? fallback : n;
}

export default function GeneralTab() {
  // The budget limits are the settings document's `budget` section (#171):
  // read as every job reads them, and written through PATCH
  // /config/settings/budget, an admin's change recorded in its history.
  const {
    data: config,
    provenance,
    isLoading: configLoading,
    isError: configError,
    refetch: refetchConfig,
  } = useBudgetSettings();
  const updateConfig = useSetBudgetLimits();
  const access = useSettingsWriteAccess();
  const navigate = useNavigate();

  const densityMode = useLayoutStore((s) => s.densityMode);
  const setDensityMode = useLayoutStore((s) => s.setDensityMode);

  const [form, setForm] = useState<BudgetLimits | null>(null);
  const [problems, setProblems] = useState<FieldProblems>({});

  // Sync budget form from config on initial load
  useEffect(() => {
    if (config) setForm((prev) => prev ?? { ...config });
  }, [config]);

  // Track unsaved changes for budget
  const hasChanges = useMemo(() => {
    if (!form || !config) return false;
    return (Object.keys(form) as (keyof BudgetLimits)[]).some(
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

  const handleChange = (field: keyof BudgetLimits, value: string | number | boolean) => {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev));
    setProblems((prev) => ({ ...prev, [field]: undefined }));
  };

  const handleNumberChange = (field: keyof BudgetLimits, value: string, min?: number, max?: number) => {
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
    // Only the fields that changed: the rest of the document stays as stored.
    const changes: Partial<BudgetLimits> = {};
    for (const key of Object.keys(form) as (keyof BudgetLimits)[]) {
      if (form[key] !== config[key]) {
        Object.assign(changes, { [key]: form[key] });
      }
    }
    updateConfig.mutate(changes, {
      onSuccess: () => {
        setForm(null);
        setProblems({});
        toast.success("Settings saved");
      },
      onError: (e) => {
        // A 422 names each refused key; show it next to its field.
        const found: FieldProblems = {};
        const rest: string[] = [];
        for (const problem of documentProblems(e) ?? []) {
          const field = problem.key?.startsWith(`${BUDGET_SECTION}.`)
            ? (problem.key.slice(BUDGET_SECTION.length + 1) as keyof BudgetLimits)
            : null;
          if (field && field in form) found[field] = problem.msg;
          else rest.push(problem.key ? `${problem.key}: ${problem.msg}` : problem.msg);
        }
        setProblems(found);
        if (rest.length > 0) toast.error(`Failed: ${rest.join("; ")}`);
        else if (Object.keys(found).length === 0) toastApiError(e, "Failed");
      },
    });
  };

  const handleReset = () => {
    if (config) setForm({ ...config });
    setProblems({});
  };

  const readOnly = !access.canWrite;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div />
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReset} disabled={!hasChanges}>
            Reset
          </Button>
          <Button onClick={handleSave} disabled={readOnly || updateConfig.isPending || !hasChanges}>
            {updateConfig.isPending ? "Saving..." : "Save changes"}
          </Button>
        </div>
      </div>

      {hasChanges && (
        <div className="text-sm text-warning">You have unsaved changes.</div>
      )}
      {readOnly && access.reason && (
        <p className="text-sm text-muted-foreground" data-testid="budget-read-only">
          {access.reason}
        </p>
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
                min="0"
                disabled={readOnly}
                aria-invalid={problems.max_budget_per_job_usd ? true : undefined}
                aria-describedby={problems.max_budget_per_job_usd ? "max_budget_per_job_usd-problem" : undefined}
                value={form.max_budget_per_job_usd}
                onChange={(e) => handleNumberChange("max_budget_per_job_usd", e.target.value, 0)}
              />
              <Provenance field="max_budget_per_job_usd" provenance={provenance} />
              <FieldProblem field="max_budget_per_job_usd" problems={problems} />
            </div>
            <div>
              <Label htmlFor="daily_cap_usd">Daily budget cap (USD)</Label>
              <Input
                id="daily_cap_usd"
                type="number"
                step="1"
                min="0"
                disabled={readOnly}
                aria-invalid={problems.daily_cap_usd ? true : undefined}
                aria-describedby={problems.daily_cap_usd ? "daily_cap_usd-problem" : undefined}
                value={form.daily_cap_usd}
                onChange={(e) => handleNumberChange("daily_cap_usd", e.target.value, 0)}
              />
              <Provenance field="daily_cap_usd" provenance={provenance} />
              <FieldProblem field="daily_cap_usd" problems={problems} />
            </div>
            <div>
              <Label htmlFor="monthly_cap_usd">Monthly budget cap (USD)</Label>
              <Input
                id="monthly_cap_usd"
                type="number"
                step="1"
                min="0"
                disabled={readOnly}
                aria-invalid={problems.monthly_cap_usd ? true : undefined}
                aria-describedby={problems.monthly_cap_usd ? "monthly_cap_usd-problem" : undefined}
                value={form.monthly_cap_usd}
                onChange={(e) => handleNumberChange("monthly_cap_usd", e.target.value, 0)}
              />
              <Provenance field="monthly_cap_usd" provenance={provenance} />
              <FieldProblem field="monthly_cap_usd" problems={problems} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground mt-3">
            0 sets no limit. The unattended jobs' caps and how far a person may continue a job past its cap are
            further keys of the same <code className="font-mono">budget</code> section, on the Policy tab.
          </p>
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
              <Label htmlFor="rate_limit_per_author_per_hour">Rate limit (per author/hour, 0 for none)</Label>
              <Input
                id="rate_limit_per_author_per_hour"
                type="number"
                min="0"
                step="1"
                disabled={readOnly}
                aria-invalid={problems.rate_limit_per_author_per_hour ? true : undefined}
                aria-describedby={
                  problems.rate_limit_per_author_per_hour ? "rate_limit_per_author_per_hour-problem" : undefined
                }
                value={form.rate_limit_per_author_per_hour}
                onChange={(e) => handleNumberChange("rate_limit_per_author_per_hour", e.target.value, 0)}
              />
              <Provenance field="rate_limit_per_author_per_hour" provenance={provenance} />
              <FieldProblem field="rate_limit_per_author_per_hour" problems={problems} />
            </div>
            <div>
              <Label htmlFor="overrun_mode">Overrun mode</Label>
              <Select
                id="overrun_mode"
                disabled={readOnly}
                value={form.overrun_mode}
                onChange={(e) => handleChange("overrun_mode", e.target.value)}
              >
                <option value="soft">Soft (finish the node, then pause)</option>
                <option value="hard">Hard (fail the job at the cap)</option>
              </Select>
              <Provenance field="overrun_mode" provenance={provenance} />
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
              connection tests live on the LLM providers page.
            </p>
            <Button variant="outline" onClick={() => navigate("/providers")}>
              Open LLM providers
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

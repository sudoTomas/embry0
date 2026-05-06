import { useState } from "react";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { upsertProviderOverride } from "@/api/qaDashboard";
import type { WorkspaceProviderOverride } from "@/lib/types";

interface ProvidersAdminFormProps {
  /**
   * When set, the form is in edit mode for that row — the repo input is
   * disabled and the form pre-populates from the row's values.
   */
  initial?: WorkspaceProviderOverride;
  /** Called once the upsert succeeds, with the post-write row. */
  onSaved: (row: WorkspaceProviderOverride) => void;
  /** Cancel / dismiss — does NOT save. */
  onCancel: () => void;
}

/**
 * Phase 5G admin form: edits one (repo, provider_type, config) row backed by
 * qa_workspace_provider_overrides. The config field is a JSON object the
 * orchestrator passes verbatim to load_provider() — JSON validation runs
 * before submit so the operator sees a syntax error inline rather than
 * surfacing as a 422 from the backend.
 */
export function ProvidersAdminForm({
  initial,
  onSaved,
  onCancel,
}: ProvidersAdminFormProps) {
  const isEdit = !!initial;

  const [repo, setRepo] = useState(initial?.repo ?? "");
  const [providerType, setProviderType] = useState(
    initial?.provider_type ?? "npm-workspaces-turbo",
  );
  const [configText, setConfigText] = useState(
    initial?.config !== undefined
      ? JSON.stringify(initial.config, null, 2)
      : "{}",
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function parseConfig(): { ok: true; value: Record<string, unknown> } | { ok: false; reason: string } {
    let parsed: unknown;
    try {
      parsed = JSON.parse(configText);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "invalid JSON";
      return { ok: false, reason: `Invalid JSON: ${msg}` };
    }
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      return {
        ok: false,
        reason: "Config must be a JSON object (not an array, null, or scalar).",
      };
    }
    return { ok: true, value: parsed as Record<string, unknown> };
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const trimmedRepo = repo.trim();
    if (!trimmedRepo) {
      setError("Repo is required.");
      return;
    }
    const trimmedType = providerType.trim();
    if (!trimmedType) {
      setError("Provider type is required.");
      return;
    }
    const cfg = parseConfig();
    if (!cfg.ok) {
      setError(cfg.reason);
      return;
    }

    setSaving(true);
    try {
      const row = await upsertProviderOverride(trimmedRepo, {
        provider_type: trimmedType,
        config: cfg.value,
      });
      onSaved(row);
    } catch (e) {
      const msg =
        e instanceof Error
          ? e.message
          : typeof e === "object" && e !== null && "response" in e
            ? "Server rejected the upsert"
            : "Unknown error";
      setError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="providers-admin-form"
      className="space-y-4 rounded-lg border border-white/[0.08] bg-card/40 p-4"
    >
      <div>
        <Label htmlFor="provider-repo">Repo</Label>
        <Input
          id="provider-repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          placeholder="org/repo-name"
          className="font-mono mt-1"
          disabled={isEdit}
          autoFocus={!isEdit}
        />
      </div>

      <div>
        <Label htmlFor="provider-type">Provider type</Label>
        <Input
          id="provider-type"
          value={providerType}
          onChange={(e) => setProviderType(e.target.value)}
          placeholder="npm-workspaces-turbo"
          className="font-mono mt-1"
          autoFocus={isEdit}
        />
      </div>

      <div>
        <Label htmlFor="provider-config">Config (JSON object)</Label>
        <Textarea
          id="provider-config"
          value={configText}
          onChange={(e) => setConfigText(e.target.value)}
          placeholder='{"affected_filter": "[HEAD^1]", "apps_glob": "apps/*"}'
          className="font-mono mt-1 min-h-[140px]"
          spellCheck={false}
        />
        <p className="text-xs text-muted-foreground mt-1">
          Passed verbatim to the workspace provider. Common keys for
          npm-workspaces-turbo: <code className="font-mono">affected_filter</code>,{" "}
          <code className="font-mono">apps_glob</code>,{" "}
          <code className="font-mono">ignore_globs</code>.
        </p>
      </div>

      {error && (
        <p
          data-testid="providers-admin-form-error"
          className="text-sm text-destructive"
        >
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={saving}>
          {saving ? "Saving..." : isEdit ? "Update" : "Add"}
        </Button>
      </div>
    </form>
  );
}

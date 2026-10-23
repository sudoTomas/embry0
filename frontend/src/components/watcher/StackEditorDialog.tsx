import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { useSaveWatcherStack } from "@/hooks/useWatcherStacks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Textarea } from "@/components/ui/Textarea";
import type { WatcherStack, WatcherTracker } from "@/lib/types";

const TRACKERS: { value: WatcherTracker; label: string }[] = [
  { value: "linear", label: "Linear" },
  { value: "github", label: "GitHub" },
  { value: "gitlab", label: "GitLab" },
];

const SETTINGS_SHAPE = 'Settings overrides must be a JSON object, such as {"interval_seconds": 600}.';

/** The overrides as typed: an object, or the reason it is not one. */
function parseSettings(text: string): { settings: Record<string, unknown> } | { error: string } {
  const trimmed = text.trim();
  if (trimmed === "") return { settings: {} };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return { error: SETTINGS_SHAPE };
    return { settings: parsed as Record<string, unknown> };
  } catch {
    return { error: SETTINGS_SHAPE };
  }
}

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Admin scope required.";
  if (response?.status === 422) return formatErrorDetail(response.data?.detail) ?? "The stack was not saved.";
  return null;
}

interface Props {
  /** The stack to edit; absent, the editor adds one. */
  stack?: WatcherStack;
  overridable: string[];
  onClose: () => void;
}

/**
 * Add or edit a watched stack (spec 3.7, "Watcher stacks"): the repository,
 * its Loki selector, the tracker findings are filed in (Linear needs a team
 * and a project), the settings it overrides as a JSON object, and whether it
 * is enabled. Keys the watcher does not know are named before the save and
 * again from the server's answer, which drops them.
 */
export function StackEditorDialog({ stack, overridable, onClose }: Props) {
  const [repo, setRepo] = useState(stack?.repo ?? "");
  const [logql, setLogql] = useState(stack?.logql ?? "");
  const [tracker, setTracker] = useState<WatcherTracker>(stack?.tracker ?? "linear");
  const [teamId, setTeamId] = useState(String(stack?.tracker_target.team_id ?? ""));
  const [projectId, setProjectId] = useState(String(stack?.tracker_target.project_id ?? ""));
  const [settingsText, setSettingsText] = useState(
    stack && Object.keys(stack.settings).length > 0 ? JSON.stringify(stack.settings, null, 2) : "",
  );
  const [enabled, setEnabled] = useState(stack?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const save = useSaveWatcherStack();

  const parsed = parseSettings(settingsText);
  const unknown = "settings" in parsed ? Object.keys(parsed.settings).filter((k) => !overridable.includes(k)) : [];

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const name = repo.trim().toLowerCase();
    if (!/^[^/\s]+\/[^/\s]+$/.test(name)) {
      setError("Name the repository as owner/name.");
      return;
    }
    if (!logql.trim()) {
      setError("Give the stack a LogQL selector.");
      return;
    }
    if ("error" in parsed) {
      setError(parsed.error);
      return;
    }
    setError(null);
    const tracker_target = tracker === "linear" ? { team_id: teamId.trim(), project_id: projectId.trim() } : {};
    save.mutate(
      { repo: name, body: { logql: logql.trim(), tracker, tracker_target, settings: parsed.settings, enabled } },
      {
        onSuccess: (saved) => {
          toast.success(`Saved ${saved.repo}`);
          if (saved.ignored_settings.length > 0) toast.info(`Dropped settings the watcher does not know: ${saved.ignored_settings.join(", ")}`);
          onClose();
        },
        onError: (err) => {
          const shown = refusal(err);
          if (shown) setError(shown);
          else toastApiError(err, "The stack was not saved");
        },
      },
    );
  };

  return (
    <Dialog onClose={() => !save.isPending && onClose()} title={stack ? `Edit ${stack.repo}` : "Add stack"} dismissOnBackdrop={false} className="max-w-xl">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="space-y-1">
          <Label htmlFor="stack-repo">Repository</Label>
          <Input id="stack-repo" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/name" disabled={!!stack || save.isPending} data-autofocus={!stack ? true : undefined} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="stack-logql">LogQL selector</Label>
          <Textarea id="stack-logql" value={logql} onChange={(e) => setLogql(e.target.value)} rows={2} className="font-mono text-xs" disabled={save.isPending} data-autofocus={stack ? true : undefined} />
          <p className="text-xs text-fg-muted">What the watcher reads from Loki for this stack, such as a container or app label.</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="stack-tracker">Tracker</Label>
            <Select id="stack-tracker" value={tracker} onChange={(e) => setTracker(e.target.value as WatcherTracker)} disabled={save.isPending}>
              {TRACKERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </Select>
          </div>
          {tracker === "linear" && (
            <>
              <div className="space-y-1">
                <Label htmlFor="stack-team">Linear team id</Label>
                <Input id="stack-team" value={teamId} onChange={(e) => setTeamId(e.target.value)} disabled={save.isPending} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="stack-project">Linear project id</Label>
                <Input id="stack-project" value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={save.isPending} />
              </div>
            </>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="stack-settings">Settings overrides</Label>
          <Textarea id="stack-settings" value={settingsText} onChange={(e) => setSettingsText(e.target.value)} rows={4} className="font-mono text-xs" placeholder='{"interval_seconds": 600}' disabled={save.isPending} />
          <p className="text-xs text-fg-muted">A JSON object. An absent key inherits the global default. Known keys: {overridable.join(", ") || "none"}.</p>
          {unknown.length > 0 && <p className="text-xs text-warning">Not a watcher setting, will be dropped: {unknown.join(", ")}</p>}
        </div>
        <div className="flex items-center gap-3">
          <Switch id="stack-enabled" checked={enabled} onCheckedChange={setEnabled} disabled={save.isPending} label="Enabled" />
          <span className="text-sm text-fg-secondary" aria-hidden="true">Enabled</span>
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

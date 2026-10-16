import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { useRepoPreferences, useSetRepoPreferences } from "@/hooks/useRepoPreferences";
import { useModels } from "@/hooks/useModels";
import type { PrReviewDepth, PrReviewSettings } from "@/api/repoPreferences";
import { cn } from "@/lib/utils";

const DEFAULTS: PrReviewSettings = { enabled: false, depth: "full", model: null };

const DEPTH_OPTIONS: { value: PrReviewDepth; label: string; description: string }[] = [
  {
    value: "full",
    label: "Full",
    description:
      "Install dependencies and run every dev check. With none configured, the reviewer works out the commands from the repository and suggests them.",
  },
  {
    value: "targeted",
    label: "Targeted",
    description: "Run only the dev checks whose paths the pull request touches.",
  },
  {
    value: "read_only",
    label: "Read only",
    description: "Read the code and the diff, and run nothing. Pull requests from forks are always reviewed this way.",
  },
];

interface PullRequestReviewCardProps {
  /** Full repo path; may exceed two segments (GitLab subgroups, #162). */
  repo: string;
}

/** #30: per-repo pull request review settings, saved as one object. */
export function PullRequestReviewCard({ repo }: PullRequestReviewCardProps) {
  const { data: prefs, isLoading } = useRepoPreferences(repo);
  const { models } = useModels();
  const setPrefs = useSetRepoPreferences();
  const [settings, setSettings] = useState<PrReviewSettings>(DEFAULTS);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setSettings({ ...DEFAULTS, ...(prefs?.pr_review ?? {}) });
    setDirty(false);
  }, [prefs]);

  const update = (patch: Partial<PrReviewSettings>) => {
    setSettings((current) => ({ ...current, ...patch }));
    setDirty(true);
  };

  const handleSave = () => {
    setPrefs.mutate(
      // PUT is a partial update (#350): only pr_review is sent, so the other
      // preferences are kept.
      { fullRepo: repo, update: { pr_review: settings } },
      {
        onSuccess: () => {
          toast.success("Pull request review settings saved");
          setDirty(false);
        },
        onError: (e: Error) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  return (
    <Card className="p-4 space-y-4" data-testid="pr-review-card">
      <div>
        <h3 className="text-sm font-semibold">Pull request review</h3>
        <p className="text-xs text-muted-foreground mt-1">
          When a pull request becomes ready for review, embry0 reviews it and posts its findings as comments. It
          never approves a pull request and never blocks a merge.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          role="switch"
          aria-checked={settings.enabled}
          aria-label="Review pull requests"
          disabled={isLoading}
          onClick={() => update({ enabled: !settings.enabled })}
          className={cn(
            "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-ring",
            settings.enabled ? "bg-cyan-500" : "bg-white/20",
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 rounded-full bg-white transition-transform",
              settings.enabled ? "translate-x-4" : "translate-x-0.5",
            )}
          />
        </button>
        <span className="text-sm">{settings.enabled ? "On" : "Off"}</span>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Depth</legend>
        {DEPTH_OPTIONS.map((option) => (
          <label key={option.value} className="flex items-start gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="pr-review-depth"
              value={option.value}
              checked={settings.depth === option.value}
              onChange={() => update({ depth: option.value })}
              className="mt-1"
            />
            <span>
              <span className="font-medium">{option.label}</span>
              <span className="block text-xs text-muted-foreground">{option.description}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div className="space-y-1">
        <Label htmlFor="pr-review-model">Model</Label>
        <Select
          id="pr-review-model"
          value={settings.model ?? ""}
          onChange={(e) => update({ model: e.target.value || null })}
        >
          <option value="">Default (heavy tier)</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
      </div>

      <p className="text-xs text-muted-foreground" data-testid="pr-review-webhook-note">
        The repository's webhook must send these events. GitHub: <strong>Pull requests</strong> and{" "}
        <strong>Issue comments</strong>. GitLab: <strong>Merge request events</strong> and <strong>Comments</strong>.
        Anyone with write access can ask for another review with a comment line that reads{" "}
        <code>/embry0 review</code>.
      </p>

      <Button size="sm" onClick={handleSave} disabled={setPrefs.isPending || !dirty}>
        {setPrefs.isPending ? "Saving…" : "Save review settings"}
      </Button>
    </Card>
  );
}

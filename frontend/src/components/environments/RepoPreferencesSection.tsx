import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import {
  useRepoPreferences,
  useSetRepoPreferences,
  useDeleteRepoPreferences,
} from "@/hooks/useRepoPreferences";
import type { DevCheck } from "@/api/repoPreferences";
import { useSandboxProfiles } from "@/hooks/useSandboxProfiles";
import { toastApiError } from "@/api/errorToast";

interface RepoPreferencesSectionProps {
  /** Full repo path; may exceed two segments (GitLab subgroups, #162). */
  repo: string;
}

/** Editable row shape: paths joined for a single input field. */
interface DevCheckDraft {
  name: string;
  paths: string;
  command: string;
}

function toDrafts(checks: DevCheck[] | undefined): DevCheckDraft[] {
  return (checks ?? []).map((c) => ({ name: c.name, paths: c.paths.join(", "), command: c.command }));
}

function fromDrafts(drafts: DevCheckDraft[]): DevCheck[] {
  // Rows without a command are treated as deleted (the server requires one).
  return drafts
    .filter((d) => d.command.trim())
    .map((d) => ({
      name: d.name.trim() || "check",
      paths: d.paths
        .split(",")
        .map((p) => p.trim().replace(/^\/+|\/+$/g, ""))
        .filter(Boolean),
      command: d.command.trim(),
    }));
}

export function RepoPreferencesSection({ repo }: RepoPreferencesSectionProps) {
  const { data: prefs, isLoading } = useRepoPreferences(repo);
  const { data: profiles = [], isLoading: profilesLoading } = useSandboxProfiles();
  const setPrefs = useSetRepoPreferences();
  const deletePrefs = useDeleteRepoPreferences();

  const [sandboxProfile, setSandboxProfile] = useState<string>("");
  const [languageHint, setLanguageHint] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [gitAuthorName, setGitAuthorName] = useState<string>("");
  const [gitAuthorEmail, setGitAuthorEmail] = useState<string>("");
  const [baseBranch, setBaseBranch] = useState<string>("");
  const [devChecks, setDevChecks] = useState<DevCheckDraft[]>([]);
  const [dirty, setDirty] = useState(false);

  // Sync local form with server state whenever the preferences row changes.
  useEffect(() => {
    setSandboxProfile(prefs?.sandbox_profile ?? "");
    setLanguageHint(prefs?.language_hint ?? "");
    setNotes(prefs?.notes ?? "");
    setGitAuthorName(prefs?.git_author_name ?? "");
    setGitAuthorEmail(prefs?.git_author_email ?? "");
    setBaseBranch(prefs?.base_branch ?? "");
    setDevChecks(toDrafts(prefs?.dev_checks));
    setDirty(false);
  }, [prefs]);

  const handleSave = () => {
    setPrefs.mutate(
      {
        fullRepo: repo,
        update: {
          sandbox_profile: sandboxProfile || null,
          language_hint: languageHint || null,
          notes,
          git_author_name: gitAuthorName || null,
          git_author_email: gitAuthorEmail || null,
          base_branch: baseBranch.trim() || null,
          // PUT is a partial update (#350): fields this form doesn't edit
          // (execution_mode, auth_mode, verification) are left out and kept.
          dev_checks: fromDrafts(devChecks),
        },
      },
      {
        onSuccess: () => {
          toast.success("Sandbox preferences saved");
          setDirty(false);
        },
        onError: (e: Error) => toastApiError(e, "Failed"),
      },
    );
  };

  const handleReset = () => {
    deletePrefs.mutate(
      { fullRepo: repo },
      {
        onSuccess: () => {
          toast.success("Sandbox preferences cleared");
          setSandboxProfile("");
          setLanguageHint("");
          setNotes("");
          setGitAuthorName("");
          setGitAuthorEmail("");
          setBaseBranch("");
          setDevChecks([]);
          setDirty(false);
        },
        onError: (e: Error) => toastApiError(e, "Failed"),
      },
    );
  };

  const saving = setPrefs.isPending;
  const resetting = deletePrefs.isPending;
  const hasRow = Boolean(prefs);

  return (
    <Card className="p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Sandbox preferences</h3>
        <p className="text-xs text-muted-foreground mt-1">
          Override the triage agent&apos;s sandbox profile pick for this repository. Leave blank to
          let triage decide.
        </p>
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/config`}
          className="text-xs text-fg hover:text-primary hover:underline mt-1 inline-block"
          data-testid="repo-prefs-qa-config-link"
        >
          Edit QA config →
        </Link>
      </div>

      <div className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="repo-prefs-profile">Sandbox profile</Label>
          <Select
            id="repo-prefs-profile"
            value={sandboxProfile}
            disabled={isLoading || profilesLoading}
            onChange={(e) => {
              setSandboxProfile(e.target.value);
              setDirty(true);
            }}
          >
            <option value="">— triage chooses —</option>
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1">
          <Label htmlFor="repo-prefs-language">Language hint</Label>
          <Input
            id="repo-prefs-language"
            value={languageHint}
            placeholder="python, java, rust, …"
            onChange={(e) => {
              setLanguageHint(e.target.value);
              setDirty(true);
            }}
          />
          <p className="text-xs text-muted-foreground">
            Informational only today; used to help operators identify repos at a glance.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="repo-prefs-git-name">Git author name</Label>
            <Input
              id="repo-prefs-git-name"
              value={gitAuthorName}
              placeholder="embry0 (default)"
              onChange={(e) => {
                setGitAuthorName(e.target.value);
                setDirty(true);
              }}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="repo-prefs-git-email">Git author email</Label>
            <Input
              id="repo-prefs-git-email"
              value={gitAuthorEmail}
              placeholder="embry0-bot@users.noreply.github.com (default)"
              onChange={(e) => {
                setGitAuthorEmail(e.target.value);
                setDirty(true);
              }}
            />
            <p className="text-xs text-muted-foreground">
              Set when the target org&apos;s push rules reject the default identity&apos;s email.
            </p>
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="repo-prefs-base-branch">Base branch</Label>
          <Input
            id="repo-prefs-base-branch"
            value={baseBranch}
            placeholder="dev, main, release/2026.09 — blank asks the forge"
            onChange={(e) => {
              setBaseBranch(e.target.value);
              setDirty(true);
            }}
          />
          <p className="text-xs text-muted-foreground">
            The branch jobs clone, diff against, and target with the merge/pull request. Leave
            blank to use the forge project&apos;s own default branch.
          </p>
        </div>

        <div className="space-y-2">
          <Label>Dev checks (pre-PR self-checks)</Label>
          <p className="text-xs text-muted-foreground">
            The developer agent runs these before opening a PR when the diff touches the
            listed path prefixes (blank = always). Stored in embry0 — the target repo needs no
            .e0/dev.yaml.
          </p>
          {devChecks.map((check, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_2fr_auto] gap-2" data-testid="dev-check-row">
              <Input
                value={check.name}
                placeholder="name (e.g. python)"
                aria-label="Dev check name"
                onChange={(e) => {
                  setDevChecks((rows) => rows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)));
                  setDirty(true);
                }}
              />
              <Input
                value={check.paths}
                placeholder="paths (ml, frontend)"
                aria-label="Dev check paths"
                onChange={(e) => {
                  setDevChecks((rows) => rows.map((r, j) => (j === i ? { ...r, paths: e.target.value } : r)));
                  setDirty(true);
                }}
              />
              <Input
                value={check.command}
                placeholder="command (cd ml && pytest -q)"
                aria-label="Dev check command"
                onChange={(e) => {
                  setDevChecks((rows) => rows.map((r, j) => (j === i ? { ...r, command: e.target.value } : r)));
                  setDirty(true);
                }}
              />
              <Button
                size="sm"
                variant="outline"
                aria-label="Remove dev check"
                onClick={() => {
                  setDevChecks((rows) => rows.filter((_, j) => j !== i));
                  setDirty(true);
                }}
              >
                ✕
              </Button>
            </div>
          ))}
          <Button
            size="sm"
            variant="outline"
            data-testid="dev-check-add"
            onClick={() => {
              setDevChecks((rows) => [...rows, { name: "", paths: "", command: "" }]);
              setDirty(true);
            }}
          >
            Add dev check
          </Button>
        </div>

        <div className="space-y-1">
          <Label htmlFor="repo-prefs-notes">Notes</Label>
          <Textarea
            id="repo-prefs-notes"
            value={notes}
            placeholder="Freeform notes about this repo's build/runtime quirks."
            onChange={(e) => {
              setNotes(e.target.value);
              setDirty(true);
            }}
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary" onClick={handleSave} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={handleReset}
          disabled={resetting || (!hasRow && !dirty)}
        >
          {resetting ? "Clearing…" : "Reset"}
        </Button>
      </div>
    </Card>
  );
}

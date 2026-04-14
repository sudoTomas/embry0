import { useEffect, useState } from "react";
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
import { useSandboxProfiles } from "@/hooks/useSandboxProfiles";

interface RepoPreferencesSectionProps {
  owner: string;
  repo: string;
}

export function RepoPreferencesSection({ owner, repo }: RepoPreferencesSectionProps) {
  const { data: prefs, isLoading } = useRepoPreferences(owner, repo);
  const { data: profiles = [], isLoading: profilesLoading } = useSandboxProfiles();
  const setPrefs = useSetRepoPreferences();
  const deletePrefs = useDeleteRepoPreferences();

  const [sandboxProfile, setSandboxProfile] = useState<string>("");
  const [languageHint, setLanguageHint] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [dirty, setDirty] = useState(false);

  // Sync local form with server state whenever the preferences row changes.
  useEffect(() => {
    setSandboxProfile(prefs?.sandbox_profile ?? "");
    setLanguageHint(prefs?.language_hint ?? "");
    setNotes(prefs?.notes ?? "");
    setDirty(false);
  }, [prefs]);

  const handleSave = () => {
    setPrefs.mutate(
      {
        owner,
        repo,
        update: {
          sandbox_profile: sandboxProfile || null,
          language_hint: languageHint || null,
          notes,
        },
      },
      {
        onSuccess: () => {
          toast.success("Sandbox preferences saved");
          setDirty(false);
        },
        onError: (e: Error) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const handleReset = () => {
    deletePrefs.mutate(
      { owner, repo },
      {
        onSuccess: () => {
          toast.success("Sandbox preferences cleared");
          setSandboxProfile("");
          setLanguageHint("");
          setNotes("");
          setDirty(false);
        },
        onError: (e: Error) => toast.error(`Failed: ${e.message}`),
      },
    );
  };

  const saving = setPrefs.isPending;
  const resetting = deletePrefs.isPending;
  const hasRow = Boolean(prefs);

  return (
    <Card className="p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Sandbox Preferences</h3>
        <p className="text-xs text-muted-foreground mt-1">
          Override the triage agent&apos;s sandbox profile pick for this repository. Leave blank to
          let triage decide.
        </p>
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
        <Button size="sm" onClick={handleSave} disabled={saving || !dirty}>
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

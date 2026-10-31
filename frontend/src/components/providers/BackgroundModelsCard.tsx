import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { apiErrorMessage, isConflict } from "@/api/errorDetail";
import { BACKGROUND_MODELS_SECTION } from "@/hooks/useBackgroundModels";
import { useEffectiveSettings, useSetSettingsKeys, useSettingsWriteAccess } from "@/hooks/useSettingsDocument";
import { actorInText } from "@/lib/actors";
import { SOURCE_LABELS } from "@/lib/settingsDocument";
import type { EffectiveSetting } from "@/lib/types/settings";
import { formatDateTime } from "@/lib/utils";

/** Each feature's key, in the section's order, and what it does without a model. */
const FEATURES: { key: string; label: string; none: string }[] = [
  { key: "watcher", label: "Log analyses", none: "their agents' own models" },
  { key: "context_audit", label: "Context audits", none: "their agents' own models" },
  { key: "scout", label: "Scout", none: "no model: it starts nothing" },
  { key: "proposal_brief", label: "Proposal briefs", none: "no model: none are written" },
  { key: "job_brief", label: "Job briefs", none: "no model: none are written" },
  { key: "slop_judge", label: "Slop-O-Meter judgments", none: "no model: outliers stay unjudged" },
];

/** Where the default's value comes from, and who set it when the history says. */
function provenance(setting: EffectiveSetting): string {
  const where = SOURCE_LABELS[setting.source] ?? setting.source;
  if (setting.source === "default" || (!setting.set_by && !setting.set_at)) return where;
  const when = setting.set_at ? ` on ${formatDateTime(setting.set_at)}` : "";
  return `${where} · set by ${actorInText(setting.set_by)}${when}`;
}

/** What one feature runs on, and why: its own model, none (as set, or as its default), or the default (which may be none). */
function runsOn(setting: EffectiveSetting | undefined, fallback: string, none: string): { text: string; why: string } {
  const own = setting?.value;
  if (own === false) {
    return { text: none, why: setting?.source === "default" ? "until a model is named for it" : "set to none" };
  }
  if (typeof own === "string" && own) return { text: own, why: "its own" };
  return fallback ? { text: fallback, why: "the default" } : { text: none, why: "no default set" };
}

/** The default takes only a model of the operator's own server: a preference for a hosted model would be a pin. */
const LOCAL_MODEL = /^custom\/\S+$/;

/**
 * The models embry0's background features run on: the settings document's
 * `background_models` section. The field is its `default`, changed alone
 * (`PATCH /config/settings/background_models`), so the rest of the document
 * stays as written; each feature's own key is set in Settings → Policy.
 * The default is a model of the operator's own server, as the API requires,
 * so the field offers only those: the ones the section and `localModels`
 * name. Anyone signed in sees the values and where they came from; only an
 * admin can change them.
 */
export function BackgroundModelsCard({ localModels }: { localModels: string[] }) {
  const effective = useEffectiveSettings();
  const access = useSettingsWriteAccess();
  const save = useSetSettingsKeys();
  const [draft, setDraft] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const section = effective.data?.sections.find((s) => s.name === BACKGROUND_MODELS_SECTION);
  // A backend older than the section has nothing to show here.
  if (!effective.data || !section) return null;
  const generation = effective.data.generation;
  const keys = new Map(section.keys.map((k) => [k.key, k]));
  const defaultSetting = keys.get("default");
  const saved = typeof defaultSetting?.value === "string" ? defaultSetting.value : "";
  const value = draft ?? saved;
  const changed = value.trim() !== saved;
  const valid = value.trim() === "" || LOCAL_MODEL.test(value.trim());
  const suggestions = [
    ...new Set(
      [...section.keys.map((k) => k.value), ...localModels].filter(
        (m): m is string => typeof m === "string" && LOCAL_MODEL.test(m),
      ),
    ),
  ];

  const onSave = () => {
    setProblem(null);
    save.mutate(
      {
        section: BACKGROUND_MODELS_SECTION,
        values: { default: value.trim() || null },
        generation,
      },
      {
        onSuccess: (result) => {
          setDraft(null);
          toast.success(value.trim() ? "Background model saved" : "Background model cleared");
          if (result.managed_by === "file") {
            toast.info("The settings document is kept in a file: this lasts until the file is applied again.");
          }
        },
        onError: (error) =>
          setProblem(
            isConflict(error)
              ? "The settings changed since this page read them. They are read again: check the value and save again."
              : apiErrorMessage(error, "Failed to save the background model"),
          ),
      },
    );
  };

  return (
    <Card data-testid="background-models">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Background models</CardTitle>
          <Button size="sm" onClick={onSave} disabled={!access.canWrite || !changed || !valid || save.isPending}>
            {save.isPending ? "Saving..." : "Save"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          The model of your own server, custom/&lt;model&gt;, that log analyses, context audits, briefs and
          Slop-O-Meter judgments run on unless they name their own. The scout runs only on a model named for it. In
          Settings → Policy (background_models) each feature can name its own: briefs, the judge and the scout may
          name a hosted model, and its spend counts against the daily and monthly caps.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <Label htmlFor="background_model_default">Background work runs on</Label>
          <Input
            id="background_model_default"
            className="w-80 font-mono"
            placeholder="custom/<model> (empty: none)"
            list="background_model_suggestions"
            value={value}
            disabled={!access.canWrite}
            onChange={(e) => {
              setDraft(e.target.value);
              setProblem(null);
            }}
          />
          <datalist id="background_model_suggestions">
            {suggestions.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          {defaultSetting && (
            <p className="mt-1 text-xs text-muted-foreground" data-testid="background-model-source">
              <Badge tone={defaultSetting.source === "default" ? "neutral" : "info"}>
                {provenance(defaultSetting)}
              </Badge>
            </p>
          )}
          {!valid && (
            <p className="mt-1 text-xs text-destructive">The default is a model of your own server, custom/&lt;model&gt;.</p>
          )}
          {problem && (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {problem}
            </p>
          )}
          {!access.canWrite && access.reason && <p className="mt-1 text-xs text-muted-foreground">{access.reason}</p>}
        </div>
        <ul className="space-y-1 text-xs" data-testid="background-model-features">
          {FEATURES.map((feature) => {
            const { text, why } = runsOn(keys.get(feature.key), saved, feature.none);
            return (
              <li key={feature.key} className="flex flex-wrap gap-x-2" data-testid={`background-model-${feature.key}`}>
                <span className="text-muted-foreground">{feature.label}:</span>
                <span className="font-mono">{text}</span>
                <span className="text-muted-foreground">({why})</span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

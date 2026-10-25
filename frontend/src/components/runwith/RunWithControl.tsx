import { useId, useState } from "react";
import type { RunWith, RunWithKey } from "@/api/runWith";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { useModels } from "@/hooks/useModels";
import { useRunWithKeys } from "@/hooks/useRunWith";
import { providerForModel } from "@/lib/providers";
import { providerLabel } from "@/lib/status";

const SCOPE_LABEL: Record<RunWithKey["scope"], string> = {
  repo: "this repository",
  owner: "its owner",
  global: "every repository",
};

interface RunWithControlProps {
  value: RunWith;
  onChange: (next: RunWith) => void;
  /** The repository the work runs on: it decides which stored keys the job may use. */
  repo?: string | null;
  /** Open on first render, for a form with room; otherwise a one-line summary that opens. */
  defaultOpen?: boolean;
}

/** Run with (spec 2026-10-25): pick the model every agent of the job runs, and one of the
 * stored keys for that model's provider. Left empty, the job runs as it always would.
 * Closed, it reads nothing: the models and keys load when it opens. */
export function RunWithControl({ value, onChange, repo, defaultOpen = false }: RunWithControlProps) {
  const id = useId();
  const [open, setOpen] = useState(defaultOpen);
  const [keyLabel, setKeyLabel] = useState<string | null>(null);
  const summary =
    value.model || value.credential_id
      ? [value.model, value.credential_id ? (keyLabel ?? "a chosen key") : null].filter(Boolean).join(" · ")
      : "each agent's model, its usual key";

  return (
    <div className="space-y-2" data-testid="run-with">
      <button
        type="button"
        className="text-xs text-white/60 underline decoration-dotted hover:text-white"
        aria-expanded={open}
        aria-controls={`${id}-fields`}
        onClick={() => setOpen(!open)}
      >
        Run with: {summary}
      </button>
      {open && (
        <RunWithFields id={id} value={value} onChange={onChange} repo={repo} onKeyLabel={setKeyLabel} />
      )}
    </div>
  );
}

function RunWithFields({
  id,
  value,
  onChange,
  repo,
  onKeyLabel,
}: Omit<RunWithControlProps, "defaultOpen"> & { id: string; onKeyLabel: (label: string | null) => void }) {
  const { models, providers = [] } = useModels();
  const { data: keys = [] } = useRunWithKeys(repo);
  const providerOf = (model: string) => providerForModel(model, providers)?.name ?? "anthropic";
  const offered = value.model ? keys.filter((k) => k.provider === providerOf(value.model as string)) : keys;

  const setModel = (model: string) => {
    // A key of another provider cannot serve the new model: the choice drops it.
    const chosen = keys.find((k) => k.id === value.credential_id);
    const keepKey = !!value.credential_id && (!model || chosen?.provider === providerOf(model));
    if (!keepKey) onKeyLabel(null);
    onChange({ ...(model ? { model } : {}), ...(keepKey ? { credential_id: value.credential_id } : {}) });
  };
  const setKey = (credentialId: string) => {
    onKeyLabel(keys.find((k) => k.id === credentialId)?.label ?? null);
    onChange({ ...(value.model ? { model: value.model } : {}), ...(credentialId ? { credential_id: credentialId } : {}) });
  };

  return (
    <div id={`${id}-fields`} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <Label htmlFor={`${id}-model`}>Model</Label>
        <ModelSelect
          id={`${id}-model`}
          value={value.model ?? ""}
          onChange={(e) => setModel(e.target.value)}
          models={models}
          providers={providers}
          leading={<option value="">Each agent's own model</option>}
          labelOf={(model) => `${model} (${providerLabel(providerOf(model))})`}
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={`${id}-key`}>Key</Label>
        <Select id={`${id}-key`} value={value.credential_id ?? ""} onChange={(e) => setKey(e.target.value)}>
          <option value="">The key its bindings pick</option>
          {offered.map((key) => (
            <option key={key.id} value={key.id} disabled={!!key.status && key.status !== "ok"}>
              {key.label} · {providerLabel(key.provider)} · {SCOPE_LABEL[key.scope]}
              {key.status && key.status !== "ok" ? ` (${key.status})` : ""}
            </option>
          ))}
        </Select>
      </div>
      <p className="text-xs text-muted-foreground sm:col-span-2">
        {value.model && offered.length === 0
          ? `No stored ${providerLabel(providerOf(value.model))} key reaches ${repo ?? "this work"}: its usual key runs it.`
          : "A chosen key that cannot run fails the job: another key never runs in its place."}
      </p>
    </div>
  );
}

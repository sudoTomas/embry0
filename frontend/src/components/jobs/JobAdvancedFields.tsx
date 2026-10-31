import { ModelSelect } from "@/components/models/ModelSelect";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { useAgents } from "@/hooks/useAgents";
import { useModels } from "@/hooks/useModels";
import { useSandboxProfiles } from "@/hooks/useSandboxProfiles";
import { useTemplates } from "@/hooks/usePipelines";
import type { JobCreateRequest } from "@/lib/types";

/** Advanced launch options shared by CreateJobDialog (Jobs page) and
 * NewJobForm (Console board). Everything here is optional — empty values
 * mean "let triage decide" and are omitted from the request. */
export interface JobAdvancedState {
  pipelineTemplate: string;
  sandboxProfile: string;
  maxBudget: string;
  agentModels: Record<string, string>;
}

export const EMPTY_ADVANCED: JobAdvancedState = {
  pipelineTemplate: "",
  sandboxProfile: "",
  maxBudget: "",
  agentModels: {},
};

/** Fold the advanced state into a JobCreateRequest partial, omitting unset
 * values so the backend's triage defaults keep applying. */
export function advancedToRequest(adv: JobAdvancedState): Partial<JobCreateRequest> {
  const models = Object.fromEntries(
    Object.entries(adv.agentModels).filter(([, model]) => model.trim() !== ""),
  );
  return {
    ...(adv.pipelineTemplate ? { pipeline_template: adv.pipelineTemplate } : {}),
    ...(adv.sandboxProfile ? { sandbox_profile: adv.sandboxProfile } : {}),
    ...(adv.maxBudget && Number(adv.maxBudget) > 0 ? { max_budget_usd: Number(adv.maxBudget) } : {}),
    ...(Object.keys(models).length > 0 ? { agent_models: models } : {}),
  };
}

interface JobAdvancedFieldsProps {
  value: JobAdvancedState;
  onChange: (next: JobAdvancedState) => void;
  /** id prefix so the same fields can mount twice on one page */
  idPrefix?: string;
}

export function JobAdvancedFields({ value, onChange, idPrefix = "job-adv" }: JobAdvancedFieldsProps) {
  const { data: templates } = useTemplates();
  const { data: profiles } = useSandboxProfiles();
  const { data: allAgents } = useAgents();
  // A job's per-agent models name the agents a job runs: a brief writer's voice (runs_in_jobs false) is none.
  const agents = allAgents?.filter((agent) => agent.runs_in_jobs !== false);
  const { models: knownModels, providers } = useModels();

  const set = (patch: Partial<JobAdvancedState>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-4" data-testid={`${idPrefix}-fields`}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <Label htmlFor={`${idPrefix}-template`}>Pipeline Template</Label>
          <Select
            id={`${idPrefix}-template`}
            value={value.pipelineTemplate}
            onChange={(e) => set({ pipelineTemplate: e.target.value })}
          >
            <option value="">Auto (triage decides)</option>
            {(templates ?? []).map((t) => (
              <option key={t.template_id} value={t.name}>
                {t.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`${idPrefix}-profile`}>Sandbox Profile</Label>
          <Select
            id={`${idPrefix}-profile`}
            value={value.sandboxProfile}
            onChange={(e) => set({ sandboxProfile: e.target.value })}
          >
            <option value="">Default</option>
            {(profiles ?? []).map((p) => (
              <option key={p.name} value={p.name}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor={`${idPrefix}-budget`}>Max Budget (USD)</Label>
          <Input
            id={`${idPrefix}-budget`}
            type="number"
            step="0.5"
            min="0"
            max="1000"
            placeholder="Triage decides"
            value={value.maxBudget}
            onChange={(e) => set({ maxBudget: e.target.value })}
          />
        </div>
      </div>

      <div>
        <Label>Per-Agent Model Overrides</Label>
        <p className="text-xs text-white/30 mt-0.5 mb-2">
          Leave on Auto to let triage pick. Overrides outrank the triage decision.
        </p>
        <div className="space-y-2">
          {(agents ?? []).map((agent) => (
            <div key={agent.type} className="grid grid-cols-[140px_1fr] items-center gap-3">
              <span className="text-sm text-white/60 font-mono truncate" title={agent.type}>
                {agent.type}
              </span>
              <ModelSelect
                aria-label={`Model override for ${agent.type}`}
                filterLabel={`Filter models for ${agent.type}`}
                value={value.agentModels[agent.type] ?? ""}
                onChange={(e) => {
                  const next = { ...value.agentModels };
                  if (e.target.value === "") delete next[agent.type];
                  else next[agent.type] = e.target.value;
                  set({ agentModels: next });
                }}
                models={knownModels}
                providers={providers}
                leading={<option value="">Auto</option>}
              />
            </div>
          ))}
          {(agents ?? []).length === 0 && (
            <p className="text-xs text-white/30">No agent definitions loaded.</p>
          )}
        </div>
      </div>
    </div>
  );
}

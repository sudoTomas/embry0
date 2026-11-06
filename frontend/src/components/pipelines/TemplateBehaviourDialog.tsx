import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { apiErrorMessage } from "@/api/errorDetail";
import { useAgents } from "@/hooks/useAgents";
import { useSetTemplateBehaviour } from "@/hooks/usePipelines";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import {
  DEFAULT_TEMPLATE_BEHAVIOUR,
  TEMPLATE_KINDS,
  type PipelineTemplateSummary,
  type TemplateBehaviour,
  type TemplateKind,
} from "@/lib/types";

/** The longest `delivers` the backend keeps (row_behaviour.DELIVERS_MAX). */
const DELIVERS_MAX = 300;

/**
 * What a template's row says about its jobs (#167): whether a job that names
 * it skips triage, whether triage is offered it, the kind its jobs are
 * recorded as, what they deliver and who leads them. A built-in template
 * keeps what is saved here across restarts. A value the server refuses is
 * said in the dialog, which keeps what was set, so the person corrects it in place.
 */
export function TemplateBehaviourDialog({ template, onClose }: { template: PipelineTemplateSummary; onClose: () => void }) {
  const [form, setForm] = useState<TemplateBehaviour>(template.behaviour ?? DEFAULT_TEMPLATE_BEHAVIOUR);
  const [error, setError] = useState<string | null>(null);
  const { data: agents } = useAgents();
  const save = useSetTemplateBehaviour();
  // A leader is an agent a job runs; the brief writer's voices are none.
  const leaders = (agents ?? []).filter((a) => a.runs_in_jobs !== false).map((a) => a.type);
  if (form.leader_agent && !leaders.includes(form.leader_agent)) leaders.push(form.leader_agent);
  const set = (patch: Partial<TemplateBehaviour>) => {
    setError(null);
    setForm((prev) => ({ ...prev, ...patch }));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate(
      { templateId: template.template_id, behaviour: { ...form, delivers: form.delivers.trim() } },
      {
        onSuccess: (saved) => {
          saved.warnings?.forEach((w) => toast.warning(w));
          toast.success(`Saved how ${template.name} treats its jobs`);
          onClose();
        },
        onError: (err) => setError(apiErrorMessage(err)),
      },
    );
  };

  return (
    <Dialog onClose={onClose} title={`Behaviour: ${template.name}`} className="max-w-lg">
      <form onSubmit={submit} className="space-y-4 p-5" data-testid="template-behaviour">
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor="behaviour-fixed-route">Skips triage</Label>
            <p className="text-xs text-white/40">A job that names this template runs it as it is, without asking triage.</p>
          </div>
          <Switch id="behaviour-fixed-route" checked={form.fixed_route} onCheckedChange={(fixed_route) => set({ fixed_route })} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div>
            <Label htmlFor="behaviour-offered">Offered to triage</Label>
            <p className="text-xs text-white/40">Off: triage never picks it, so it runs only when a job names it.</p>
          </div>
          <Switch
            id="behaviour-offered"
            checked={form.offered_to_triage}
            onCheckedChange={(offered_to_triage) => set({ offered_to_triage })}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="behaviour-kind">Its jobs are recorded as</Label>
          <Select
            id="behaviour-kind"
            value={form.job_kind ?? ""}
            onChange={(e) => set({ job_kind: (e.target.value || null) as TemplateKind | null })}
          >
            <option value="">The kind triage gives them</option>
            {TEMPLATE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </Select>
          <p className="text-xs text-white/40">The kind this template is the default for, when it is one, wins.</p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="behaviour-leader">Led by</Label>
          <Select id="behaviour-leader" value={form.leader_agent ?? ""} onChange={(e) => set({ leader_agent: e.target.value || null })}>
            <option value="">No one: the agents that run lead</option>
            {leaders.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </Select>
          <p className="text-xs text-white/40">
            The guild draws a job of this template as its leader before an agent starts, and credits its work to it.
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="behaviour-delivers">What its jobs deliver</Label>
          <Input
            id="behaviour-delivers"
            value={form.delivers}
            maxLength={DELIVERS_MAX}
            placeholder="e.g. a written report"
            onChange={(e) => set({ delivers: e.target.value })}
          />
          <p className="text-xs text-white/40">One line, read into each job&apos;s brief. Empty says what its kind delivers.</p>
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

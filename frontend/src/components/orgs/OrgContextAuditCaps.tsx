import { useState } from "react";
import { toast } from "sonner";
import { apiErrorMessage, isConflict } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { useOrgContextAudit } from "@/hooks/useOrgContextAudit";
import {
  BUDGET_SECTION,
  CAP_FIELDS,
  capDraft,
  capProblem,
  changedCaps,
  type Draft,
} from "@/lib/contextAuditSettings";
import { documentProblems } from "@/lib/settingsDocument";
import { Provenance } from "./OrgContextAuditSettings";

/**
 * What an organization's context audits may spend: its keys of the settings
 * document's `budget` section, in its layer (#171), changed here as the rest
 * of its context audit settings are (#170). Each field shows the value the
 * organization reads and where it comes from; "As the deployment" hands it
 * back. The fleet's own caps bound them where each job runs.
 */
export function OrgContextAuditCaps({ owner }: { owner: string }) {
  const audit = useOrgContextAudit(owner);
  const [drafts, setDrafts] = useState<Record<string, Draft> | null>(null);
  const [problems, setProblems] = useState<Record<string, string | undefined>>({});
  const caps = audit.caps;
  if (!caps) return null;

  const base = Object.fromEntries(CAP_FIELDS.map((field) => [field.key, capDraft(caps[field.key])]));
  const current = drafts ?? base;
  const changes = changedCaps(current, caps);
  const local = Object.fromEntries(
    CAP_FIELDS.map((field) => [field.key, capProblem(field, current[field.key])]).filter(([, problem]) => problem),
  );
  const shown: Record<string, string | undefined> = { ...problems, ...local };
  const readOnly = !audit.access.canWrite;
  const dirty = Object.keys(changes).length > 0;

  const edit = (key: string, next: Partial<Draft>) => {
    setDrafts({ ...current, [key]: { ...current[key], ...next } });
    setProblems((prev) => ({ ...prev, [key]: undefined }));
  };

  const onSave = () => {
    setProblems({});
    audit.save.mutate(
      { section: BUDGET_SECTION, values: changes, generation: audit.generation },
      {
        onSuccess: () => {
          setDrafts(null);
          toast.success("Context audit caps saved");
        },
        onError: (error) => {
          if (isConflict(error)) {
            toast.error("The settings changed since this page read them. They are read again: check and save again.");
            return;
          }
          const found: Record<string, string> = {};
          const rest: string[] = [];
          for (const problem of documentProblems(error) ?? []) {
            const key = problem.key?.split(".")[1];
            if (problem.key?.startsWith(`${BUDGET_SECTION}.`) && key && CAP_FIELDS.some((f) => f.key === key)) {
              found[key] = problem.msg;
            } else {
              rest.push(problem.key ? `${problem.key}: ${problem.msg}` : problem.msg);
            }
          }
          setProblems(found);
          if (rest.length > 0) toast.error(rest.join("; "));
          else if (Object.keys(found).length === 0) toast.error(apiErrorMessage(error, "The caps were not saved"));
        },
      },
    );
  };

  return (
    <div className="space-y-4" data-testid="org-context-audit-caps">
      {CAP_FIELDS.map((field) => {
        const setting = caps[field.key];
        const draft = current[field.key];
        const id = `org-context-audit-cap-${field.key}`;
        return (
          <div key={field.key} className="space-y-1" data-testid={id}>
            <Label htmlFor={`${id}-mode`}>{field.label}</Label>
            <div className="flex flex-wrap items-start gap-2">
              <Select
                id={`${id}-mode`}
                className="w-64"
                value={draft.mode}
                disabled={readOnly}
                onChange={(e) => edit(field.key, { mode: e.target.value })}
              >
                <option value="inherit">{`As the deployment (${String(setting?.inherited ?? setting?.value ?? "")})`}</option>
                <option value="own">{`${owner}'s own`}</option>
              </Select>
              {draft.mode === "own" && (
                <Input
                  aria-label={field.label}
                  inputMode={field.count ? "numeric" : "decimal"}
                  className="w-24"
                  value={draft.text}
                  disabled={readOnly}
                  onChange={(e) => edit(field.key, { text: e.target.value })}
                />
              )}
            </div>
            <p className="text-xs text-fg-muted">{field.help}</p>
            <Provenance owner={owner} setting={setting} testId={`${id}-source`} />
            {shown[field.key] && (
              <p className="text-xs text-destructive" role="alert" data-testid={`${id}-problem`}>
                {shown[field.key]}
              </p>
            )}
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onSave}
          disabled={readOnly || !dirty || Object.keys(local).length > 0 || audit.save.isPending}
          data-testid="org-context-audit-caps-save"
        >
          {audit.save.isPending ? "Saving…" : "Save caps"}
        </Button>
        {dirty && (
          <Button type="button" size="sm" variant="ghost" onClick={() => setDrafts(null)} disabled={audit.save.isPending}>
            Reset
          </Button>
        )}
      </div>
    </div>
  );
}

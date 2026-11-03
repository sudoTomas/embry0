import { useState, type ReactNode } from "react";
import { toast } from "sonner";
import { apiErrorMessage, isConflict } from "@/api/errorDetail";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { useOrgContextAudit } from "@/hooks/useOrgContextAudit";
import { actorInText } from "@/lib/actors";
import {
  CONTEXT_AUDIT_SECTION,
  ORG_CONTEXT_AUDIT_KEYS,
  changedValues,
  draftProblem,
  initialDraft,
  listText,
  pullRequestsText,
  scheduleText,
  type Draft,
  type OrgContextAuditKey,
} from "@/lib/contextAuditSettings";
import { documentProblems, notImportedText } from "@/lib/settingsDocument";
import type { EffectiveSetting } from "@/lib/types/settings";
import { formatDateTime } from "@/lib/utils";

/** The import at upgrade writes an organization's old settings into its layer as this actor (#170). */
const IMPORT_ACTOR = "embry0:bootstrap";

type Drafts = Record<OrgContextAuditKey, Draft>;
type Problems = Partial<Record<OrgContextAuditKey, string>>;

/** Where a key's value comes from: the organization's layer (and who set it), the deployment's, or the default. */
function provenanceText(owner: string, setting: EffectiveSetting): string {
  if (setting.source === "not_imported") return notImportedText(setting.not_imported);
  if (setting.source === "default") return "Default";
  const when = setting.set_at ? ` on ${formatDateTime(setting.set_at)}` : "";
  const who = setting.set_by || setting.set_at ? ` by ${actorInText(setting.set_by)}${when}` : "";
  if (setting.source === "organization") {
    if (setting.set_by === IMPORT_ACTOR) return `Imported from ${owner}'s own settings${when}`;
    return `Set for ${owner}${who}`;
  }
  return `The deployment's${who}`;
}

export function Provenance({
  owner,
  setting,
  testId,
}: {
  owner: string;
  setting: EffectiveSetting | undefined;
  testId: string;
}) {
  if (!setting) return null;
  const tone = setting.source === "default" ? "neutral" : setting.source === "not_imported" ? "warning" : "info";
  return (
    <p className="mt-1 text-xs" data-testid={testId}>
      <Badge tone={tone}>{provenanceText(owner, setting)}</Badge>
    </p>
  );
}

function Field({
  id,
  label,
  help,
  children,
  owner,
  setting,
  problem,
}: {
  id: string;
  label: string;
  help: string;
  children: ReactNode;
  owner: string;
  setting: EffectiveSetting | undefined;
  problem: string | undefined;
}) {
  return (
    <div className="space-y-1" data-testid={`org-context-audit-${id}`}>
      <Label htmlFor={`org-context-audit-${id}-mode`}>{label}</Label>
      <div className="flex flex-wrap items-start gap-2">{children}</div>
      <p className="text-xs text-fg-muted">{help}</p>
      <Provenance owner={owner} setting={setting} testId={`org-context-audit-${id}-source`} />
      {problem && (
        <p className="text-xs text-destructive" role="alert" data-testid={`org-context-audit-${id}-problem`}>
          {problem}
        </p>
      )}
    </div>
  );
}

/**
 * An organization's own context audit policy: its keys of the settings
 * document's `context_audit` section (#170), in its layer. Each field shows
 * the value the organization reads and where it comes from; "As the
 * deployment" hands a key back to the deployment's document. A save sends
 * only the keys it changes (`PATCH /config/settings/context_audit?owner=`),
 * against the generation of the layer as read, so a change someone made in
 * between is refused rather than overwritten. Anyone signed in sees the
 * values; only those the server lets write the layer can change them.
 */
export function OrgContextAuditSettings({ owner }: { owner: string }) {
  const audit = useOrgContextAudit(owner);
  const [drafts, setDrafts] = useState<Drafts | null>(null);
  const [problems, setProblems] = useState<Problems>({});
  const settings = audit.settings;

  if (audit.isError) {
    return (
      <p className="text-sm text-destructive" data-testid="org-context-audit-error">
        The context audit settings did not load.
      </p>
    );
  }
  if (audit.isLoading) return <Skeleton className="h-32 w-full" />;
  // A server older than the section has nothing to show here.
  if (!settings) return null;

  const base = Object.fromEntries(
    ORG_CONTEXT_AUDIT_KEYS.map((key) => [key, initialDraft(key, settings[key])]),
  ) as Drafts;
  const current = drafts ?? base;
  const changes = changedValues(current, settings);
  const local = Object.fromEntries(
    ORG_CONTEXT_AUDIT_KEYS.map((key) => [key, draftProblem(key, current[key])]).filter(([, problem]) => problem),
  ) as Problems;
  const shown: Problems = { ...problems, ...local };
  const readOnly = !audit.access.canWrite;
  const dirty = Object.keys(changes).length > 0;

  const edit = (key: OrgContextAuditKey, next: Partial<Draft>) => {
    setDrafts({ ...current, [key]: { ...current[key], ...next } });
    setProblems((prev) => ({ ...prev, [key]: undefined }));
  };

  const onSave = () => {
    setProblems({});
    audit.save.mutate(
      { section: CONTEXT_AUDIT_SECTION, values: changes, generation: audit.generation },
      {
        onSuccess: (result) => {
          setDrafts(null);
          toast.success("Context audit settings saved");
          if (result.managed_by === "file") {
            toast.info("The settings document is kept in a file: this lasts until the file is applied again.");
          }
        },
        onError: (error) => {
          if (isConflict(error)) {
            toast.error("The settings changed since this page read them. They are read again: check and save again.");
            return;
          }
          const found: Problems = {};
          const rest: string[] = [];
          for (const problem of documentProblems(error) ?? []) {
            const key = problem.key?.split(".")[1] as OrgContextAuditKey | undefined;
            if (problem.key?.startsWith(`${CONTEXT_AUDIT_SECTION}.`) && key && ORG_CONTEXT_AUDIT_KEYS.includes(key)) {
              found[key] = problem.msg;
            } else {
              rest.push(problem.key ? `${problem.key}: ${problem.msg}` : problem.msg);
            }
          }
          setProblems(found);
          if (rest.length > 0) toast.error(rest.join("; "));
          else if (Object.keys(found).length === 0) toast.error(apiErrorMessage(error, "The settings were not saved"));
        },
      },
    );
  };

  const inherited = (key: OrgContextAuditKey) => settings[key]?.inherited;

  return (
    <div className="space-y-4" data-testid="org-context-audit-settings">
      <p className="text-xs text-fg-muted">
        Settings of {owner}&apos;s repositories, in its layer of the settings document: as the deployment, its value
        holds until {owner} sets its own. A repository&apos;s own layer, on the Policy tab, goes over both.
      </p>

      <Field
        id="days"
        label="Schedule"
        help="Each repository is audited when its instruction files change, and again after this many days once its branch has moved."
        owner={owner}
        setting={settings.days}
        problem={shown.days}
      >
        <Select
          id="org-context-audit-days-mode"
          className="w-64"
          value={current.days.mode}
          disabled={readOnly}
          onChange={(e) => edit("days", { mode: e.target.value })}
        >
          <option value="inherit">{`As the deployment (${scheduleText(inherited("days"))})`}</option>
          <option value="off">Off</option>
          <option value="every">Every … days</option>
        </Select>
        {current.days.mode === "every" && (
          <Input
            aria-label="Days between audits"
            inputMode="numeric"
            className="w-20"
            value={current.days.text}
            disabled={readOnly}
            onChange={(e) => edit("days", { text: e.target.value })}
            data-testid="org-context-audit-days-input"
          />
        )}
      </Field>

      <Field
        id="open_prs"
        label="Pull requests"
        help="An audit that finds outdated claims commits the fixes it checked on a branch of its own and opens a pull request for a person to review and merge, one at a time per repository. Off, the findings stay in the audit's report."
        owner={owner}
        setting={settings.open_prs}
        problem={shown.open_prs}
      >
        <Select
          id="org-context-audit-open_prs-mode"
          className="w-64"
          value={current.open_prs.mode}
          disabled={readOnly}
          onChange={(e) => edit("open_prs", { mode: e.target.value })}
        >
          <option value="inherit">{`As the deployment (${pullRequestsText(inherited("open_prs"))})`}</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </Select>
      </Field>

      <Field
        id="files"
        label="Instruction files"
        help="The globs of the files an audit reads, one per line: **/ matches any number of directories, * any characters within one. A directory with no wildcard, such as .kiro/steering, takes every file under it."
        owner={owner}
        setting={settings.files}
        problem={shown.files}
      >
        <Select
          id="org-context-audit-files-mode"
          className="w-64"
          value={current.files.mode}
          disabled={readOnly}
          onChange={(e) => edit("files", { mode: e.target.value })}
        >
          <option value="inherit">As the deployment</option>
          <option value="own">{`${owner}'s own list`}</option>
        </Select>
        {current.files.mode === "own" ? (
          <Textarea
            aria-label="Instruction file globs, one per line"
            className="min-w-64 flex-1 font-mono text-xs"
            rows={6}
            value={current.files.text}
            disabled={readOnly}
            onChange={(e) => edit("files", { text: e.target.value })}
          />
        ) : (
          <span className="text-xs text-fg-secondary [overflow-wrap:anywhere]" data-testid="org-context-audit-files-inherited">
            {listText(inherited("files"))}
          </span>
        )}
      </Field>

      <Field
        id="exclude_dirs"
        label="Skipped directories"
        help="Directory names never searched, at any depth, separated by commas: vendored and generated trees."
        owner={owner}
        setting={settings.exclude_dirs}
        problem={shown.exclude_dirs}
      >
        <Select
          id="org-context-audit-exclude_dirs-mode"
          className="w-64"
          value={current.exclude_dirs.mode}
          disabled={readOnly}
          onChange={(e) => edit("exclude_dirs", { mode: e.target.value })}
        >
          <option value="inherit">As the deployment</option>
          <option value="own">{`${owner}'s own list`}</option>
        </Select>
        {current.exclude_dirs.mode === "own" ? (
          <Input
            aria-label="Skipped directories, separated by commas"
            className="min-w-64 flex-1 font-mono text-xs"
            value={current.exclude_dirs.text}
            disabled={readOnly}
            onChange={(e) => edit("exclude_dirs", { text: e.target.value })}
          />
        ) : (
          <span className="text-xs text-fg-secondary [overflow-wrap:anywhere]" data-testid="org-context-audit-exclude_dirs-inherited">
            {listText(inherited("exclude_dirs"))}
          </span>
        )}
      </Field>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onSave}
          disabled={readOnly || !dirty || Object.keys(local).length > 0 || audit.save.isPending}
          data-testid="org-context-audit-save"
        >
          {audit.save.isPending ? "Saving…" : "Save"}
        </Button>
        {dirty && (
          <Button type="button" size="sm" variant="ghost" onClick={() => setDrafts(null)} disabled={audit.save.isPending}>
            Reset
          </Button>
        )}
        {readOnly && audit.access.reason && (
          <span className="text-xs text-fg-muted" data-testid="org-context-audit-read-only">
            {audit.access.reason}
          </span>
        )}
      </div>
    </div>
  );
}

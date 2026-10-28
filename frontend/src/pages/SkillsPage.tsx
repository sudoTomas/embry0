import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { formatErrorDetail } from "@/api/errorDetail";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import {
  anySourceSyncing,
  useCreateSkillSource,
  useDeleteInlineSkill,
  useDeleteSkillSource,
  useInlineSkills,
  usePutInlineSkill,
  useSkills,
  useSkillSources,
  useSyncSkillSource,
  useUpdateSkillSource,
} from "@/hooks/useSkills";
import type { InlineSkill, SkillSource, SkillWriteConflict } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { useIsAdmin } from "@/stores/authStore";

type HttpError = { response?: { status?: number; data?: { detail?: unknown } } };

function errorDetail(err: unknown): string {
  const detail = (err as HttpError)?.response?.data?.detail;
  return formatErrorDetail(detail) ?? (err instanceof Error ? err.message : "The request failed.");
}

/**
 * A 409 whose detail is an object names what still references the row; a
 * string detail (a sync is running) and anything else read as the detail.
 */
function writeRefusal(err: unknown): string {
  const response = (err as HttpError)?.response;
  const detail = (response?.data as SkillWriteConflict | undefined)?.detail;
  if (response?.status === 409 && detail && typeof detail === "object") {
    const agents = detail.agents ?? [];
    const templates = detail.templates ?? [];
    const refs = `Still referenced by agents ${agents.join(", ") || "none"} and templates ${templates.join(", ") || "none"}`;
    return detail.message ? `${detail.message}. ${refs}` : refs;
  }
  return errorDetail(err);
}

const STATUS_TONE: Record<string, BadgeTone> = {
  ready: "success",
  error: "error",
  pending: "warning",
  syncing: "info",
  disabled: "neutral",
};

function Alert({ children }: { children: string }) {
  return (
    <div role="alert" className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
      {children}
    </div>
  );
}

const TH = "px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-muted-foreground";
const TD = "px-3 py-2 align-top";

function SourcesSection({ sources, canWrite }: { sources: SkillSource[]; canWrite: boolean }) {
  const update = useUpdateSkillSource();
  const sync = useSyncSkillSource();
  const deleteSource = useDeleteSkillSource();
  const [refusal, setRefusal] = useState<string | null>(null);

  async function remove(id: string) {
    setRefusal(null);
    try {
      await deleteSource.mutateAsync(id);
      toast.success(`Removed source "${id}"`);
    } catch (err) {
      setRefusal(writeRefusal(err));
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Sources</h2>
        {anySourceSyncing(sources) && <span className="text-xs text-muted-foreground">syncing…</span>}
      </div>
      {refusal && <Alert>{refusal}</Alert>}
      <div className="overflow-x-auto">
        <table aria-label="Skill sources" className="w-full text-sm">
          <thead>
            <tr>
              {["Id", "Kind", "URL and ref", "Sha", "Status", "Skills", "Synced", "Enabled", "Actions"].map((h) => (
                <th key={h} className={TH}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id} aria-label={s.id} className="border-t border-white/[0.06]">
                <td className={`${TD} font-mono`}>{s.id}</td>
                <td className={TD}>{s.kind}</td>
                <td className={`${TD} break-all font-mono text-xs`}>
                  {s.url ?? "—"}
                  {s.ref && <span className="text-muted-foreground"> @ {s.ref}</span>}
                </td>
                <td className={`${TD} font-mono text-xs`}>{s.resolved_sha?.slice(0, 12) ?? "—"}</td>
                <td className={TD}>
                  <Badge tone={STATUS_TONE[s.status] ?? "neutral"}>{s.status}</Badge>
                  {s.status === "error" && s.error && <p className="mt-1 text-xs text-red-300">{s.error}</p>}
                </td>
                <td className={TD}>{`${s.skills.length} skill${s.skills.length === 1 ? "" : "s"}`}</td>
                <td className={`${TD} text-xs`}>{s.synced_at ? formatDate(s.synced_at) : "never"}</td>
                <td className={TD}>
                  <input
                    type="checkbox"
                    aria-label="Enabled"
                    checked={s.enabled}
                    disabled={!canWrite || update.isPending}
                    onChange={() =>
                      update.mutate({ id: s.id, body: { enabled: !s.enabled } }, { onError: (err) => toast.error(errorDetail(err)) })
                    }
                  />
                </td>
                <td className={TD}>
                  <div className="flex gap-2">
                    <Button type="button" size="sm" variant="outline" disabled={!canWrite || sync.isPending} onClick={() => sync.mutate(s.id, { onError: (err) => toast.error(errorDetail(err)) })}>
                      Sync
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={s.is_builtin || !canWrite}
                      title={s.is_builtin ? "Builtin sources cannot be removed" : undefined}
                      onClick={() => void remove(s.id)}
                    >
                      Remove
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function AddSourceSection({ canWrite }: { canWrite: boolean }) {
  const create = useCreateSkillSource();
  const [id, setId] = useState("");
  const [kind, setKind] = useState<"git" | "path">("git");
  const [url, setUrl] = useState("");
  const [ref, setRef] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await create.mutateAsync(kind === "git" ? { id, kind, url, ref, enabled } : { id, kind, url, enabled });
      toast.success(`Added source "${id}"`);
      setId("");
      setUrl("");
      setRef("");
      setEnabled(true);
    } catch (err) {
      setError(errorDetail(err));
    }
  }

  return (
    <Card className="p-4">
      <form onSubmit={(e) => void submit(e)} className="space-y-3">
        <h2 className="text-lg font-semibold">Add source</h2>
        {error && <Alert>{error}</Alert>}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="src-id">Id</Label>
            <Input id="src-id" value={id} disabled={!canWrite} onChange={(e) => setId(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="src-kind">Kind</Label>
            <Select id="src-kind" value={kind} disabled={!canWrite} onChange={(e) => setKind(e.target.value as "git" | "path")}>
              <option value="git">git</option>
              <option value="path">path</option>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="src-url">{kind === "git" ? "URL" : "Path"}</Label>
            <Input id="src-url" value={url} disabled={!canWrite} onChange={(e) => setUrl(e.target.value)} />
          </div>
          {kind === "git" && (
            <div className="space-y-1">
              <Label htmlFor="src-ref">Ref</Label>
              <Input id="src-ref" value={ref} disabled={!canWrite} onChange={(e) => setRef(e.target.value)} />
            </div>
          )}
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} disabled={!canWrite} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled after sync
        </label>
        <Button type="submit" size="sm" disabled={!canWrite || !id || !url || create.isPending}>
          Add source
        </Button>
      </form>
    </Card>
  );
}

function InlineSection({ skills, canWrite }: { skills: InlineSkill[]; canWrite: boolean }) {
  const put = usePutInlineSkill();
  const deleteInline = useDeleteInlineSkill();
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);

  function load(skill: InlineSkill | null) {
    setEditing(skill?.name ?? null);
    setName(skill?.name ?? "");
    setDescription(skill?.description ?? "");
    setBody(skill?.body ?? "");
    setError(null);
  }

  async function save() {
    setError(null);
    if (editing === null && skills.some((s) => s.name === name)) {
      setError(`A skill named "${name}" exists; open it from the list to edit it.`);
      return;
    }
    try {
      await put.mutateAsync({ name, body: { description, body } });
      toast.success(`Saved skill "${name}"`);
      setEditing(name);
    } catch (err) {
      setError(errorDetail(err));
    }
  }

  async function remove() {
    if (!editing) return;
    setError(null);
    try {
      await deleteInline.mutateAsync(editing);
      toast.success(`Deleted skill "${editing}"`);
      load(null);
    } catch (err) {
      setError(writeRefusal(err));
    }
  }

  return (
    <Card className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Inline skills</h2>
        <Button type="button" size="sm" variant="outline" disabled={!canWrite} onClick={() => load(null)}>
          New skill
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">Published as the builtin <code>local</code> source.</p>
      {error && <Alert>{error}</Alert>}
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <ul className="space-y-1">
          {skills.length === 0 && <li className="text-sm text-muted-foreground">No inline skills yet.</li>}
          {skills.map((s) => (
            <li key={s.name}>
              <button
                type="button"
                onClick={() => load(s)}
                className={`w-full rounded-md px-3 py-2 text-left hover:bg-white/[0.04] ${editing === s.name ? "bg-primary/[0.08]" : ""}`}
              >
                <span className="block font-mono text-sm">{s.name}</span>
                <span className="block text-xs text-muted-foreground">{s.description}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="inline-name">Name</Label>
            <Input id="inline-name" value={name} disabled={!canWrite || editing !== null} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="inline-description">Description</Label>
            <Input id="inline-description" value={description} disabled={!canWrite} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="inline-body">Body</Label>
            <Textarea id="inline-body" rows={14} className="font-mono" value={body} disabled={!canWrite} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <Button type="button" size="sm" disabled={!canWrite || !name || put.isPending} onClick={() => void save()}>
              Save skill
            </Button>
            {editing !== null && (
              <Button type="button" size="sm" variant="outline" disabled={!canWrite} onClick={() => void remove()}>
                Delete
              </Button>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

export function SkillsPage() {
  const canWrite = useIsAdmin();

  const sourcesQuery = useSkillSources();
  const catalogQuery = useSkills();
  const inlineQuery = useInlineSkills();

  if (sourcesQuery.isError) return <PageError message="Could not load skill sources." />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="title-page text-xl font-bold">Skills</h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Skill sources are synced into the shared store and mounted read-only into every sandbox; agents name skills
          as <code>plugin:skill</code>.
        </p>
      </div>

      {sourcesQuery.isLoading ? (
        <TableSkeleton columns={9} />
      ) : (
        <SourcesSection sources={sourcesQuery.data ?? []} canWrite={canWrite} />
      )}
      <AddSourceSection canWrite={canWrite} />
      {inlineQuery.isLoading ? (
        <TableSkeleton />
      ) : (
        <InlineSection skills={inlineQuery.data ?? []} canWrite={canWrite} />
      )}

      <Card className="space-y-3 p-4">
        <h2 className="text-lg font-semibold">Catalog</h2>
        {catalogQuery.isLoading ? (
          <TableSkeleton columns={4} />
        ) : (
          <div className="overflow-x-auto">
            <table aria-label="Skill catalog" className="w-full text-sm">
              <thead>
                <tr>
                  {["Name", "Source", "Description", "Availability"].map((h) => (
                    <th key={h} className={TH}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(catalogQuery.data ?? []).map((c) => (
                  <tr key={c.name} aria-label={c.name} className="border-t border-white/[0.06]">
                    <td className={`${TD} font-mono`}>{c.name}</td>
                    <td className={`${TD} font-mono`}>{c.source_id}</td>
                    <td className={TD}>{c.description}</td>
                    <td className={TD}>
                      {c.available ? (
                        <Badge tone="success">available</Badge>
                      ) : (
                        <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{c.status}</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

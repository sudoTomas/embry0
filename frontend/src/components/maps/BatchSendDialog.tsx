import { useMemo, useState } from "react";
import { toast } from "sonner";
import type { MapGraph, MapOwner, SendKind } from "@/api/maps";
import { toastApiError } from "@/api/errorToast";
import { type RunWith, runWithBody } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { useSendParts } from "@/hooks/useMaps";
import { cannotSend, partRepo } from "@/lib/mapView";

/** Parts one send carries; the server's own cap. */
export const MAX_BATCH = 200;

const KINDS: { id: SendKind; label: string; says: string }[] = [
  { id: "remove", label: "Remove", says: "Each part is deleted with what only it uses. The marks are the decision: the agent does not re-assess them." },
  { id: "fix", label: "Fix", says: "The smallest fix the evidence calls for, with a test where the repository has tests." },
  { id: "analysis", label: "Analyse", says: "Read-only: what each part does and what to do, with the change as a diff, not applied." },
];

interface BatchSendDialogProps {
  slug: string;
  /** Which owner's map of the slug (`?owner=` on the page); undefined lets the server pick the only one. */
  owner?: MapOwner;
  graph: MapGraph;
  ids: string[];
  initialKind: SendKind;
  title: string;
  by: string;
  onClose: () => void;
}

/**
 * Knowledge maps (spec 2026-10-27): send many parts at once. One job per repository (one pull request) by default,
 * so a hundred backup tables are one migration, not a hundred. Parts that cannot go for the chosen kind are listed
 * with the reason and left out; the rest can be unticked one by one.
 */
export function BatchSendDialog({ slug, owner, graph, ids, initialKind, title, by, onClose }: BatchSendDialogProps) {
  const send = useSendParts(slug, owner);
  const [kind, setKind] = useState<SendKind>(initialKind);
  const [together, setTogether] = useState(true);
  const [runWith, setRunWith] = useState<RunWith>({});
  const [note, setNote] = useState("");
  const [off, setOff] = useState<ReadonlySet<string>>(new Set());
  const nodes = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const picked = ids.slice(0, MAX_BATCH);
  const rows = picked.map((id) => {
    const n = nodes.get(id);
    return { id, n, why: n ? cannotSend(n, graph.repo, kind) : "not on the map" };
  });
  const going = rows.filter((r) => !r.why && !off.has(r.id));
  const repos = [...new Set(going.map((r) => (r.n ? partRepo(r.n, graph.repo) : null)).filter(Boolean))] as string[];
  const jobs = together ? repos.length : going.length;

  const go = async () => {
    try {
      const r = await send.mutateAsync({ node_ids: going.map((x) => x.id), kind, note, by, together, ...runWithBody(runWith) });
      const failed = r.results.filter((x) => !x.job_id);
      const started = new Set(r.results.map((x) => x.job_id).filter(Boolean)).size;
      if (failed.length) toast.error(`${r.sent} sent in ${started} job${started === 1 ? "" : "s"}; ${failed.length} not: ${failed[0].error}`);
      else toast.success(`${r.sent} sent in ${started} job${started === 1 ? "" : "s"}`);
      onClose();
    } catch (e) {
      toastApiError(e as Error, "Not sent");
    }
  };

  return (
    <Dialog onClose={onClose} title={title} className="max-w-2xl">
      <div className="space-y-3 p-4 text-sm" data-testid="batch-send">
        {ids.length > MAX_BATCH && (
          <p className="text-warning">The first {MAX_BATCH} of {ids.length} are here; send the rest in another batch.</p>
        )}
        <div className="flex gap-1.5" role="group" aria-label="What the agents do">
          {KINDS.map((k) => (
            <Button key={k.id} size="xs" variant={kind === k.id ? "default" : "outline"} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
              {k.label}
            </Button>
          ))}
        </div>
        <p className="text-xs text-fg-muted">{KINDS.find((k) => k.id === kind)?.says}</p>
        <ul className="max-h-64 space-y-0.5 overflow-auto rounded border border-border p-2 text-xs" aria-label="The parts">
          {rows.map(({ id, n, why }) => (
            <li key={id} className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                aria-label={n?.label ?? id}
                checked={!why && !off.has(id)}
                disabled={!!why}
                onChange={(e) => {
                  const next = new Set(off);
                  if (e.target.checked) next.delete(id);
                  else next.add(id);
                  setOff(next);
                }}
              />
              <span className={why ? "text-fg-muted line-through" : "text-fg"}>{n?.label ?? id}</span>
              <span className="text-fg-muted">{n ? `${n.kind}${n && partRepo(n, graph.repo) ? ` · ${partRepo(n, graph.repo)}` : ""}` : ""}</span>
              {why && <span className="ml-auto text-fg-muted">{why}</span>}
            </li>
          ))}
        </ul>
        <label className="flex items-center gap-2 text-xs text-fg-secondary">
          <Switch checked={together} onCheckedChange={setTogether} label="One job per repository" />
          One job per repository (one pull request), not one per part
        </label>
        <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="A note for the agents (optional)" aria-label="A note for the agents" />
        <RunWithControl value={runWith} onChange={setRunWith} repo={repos.length === 1 ? repos[0] : null} />
        <div className="flex items-center justify-end gap-2">
          <span className="mr-auto text-xs text-fg-muted">
            {going.length} part{going.length === 1 ? "" : "s"} · {jobs} job{jobs === 1 ? "" : "s"}
          </span>
          <Button variant="ghost" onClick={onClose}>Not now</Button>
          <Button disabled={send.isPending || going.length === 0} onClick={() => void go()}>
            {send.isPending ? "Sending…" : `Send ${going.length}`}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

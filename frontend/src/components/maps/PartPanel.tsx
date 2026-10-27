import { useEffect, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import type { MarkView, PartView, SendKind, Verdict } from "@/api/maps";
import { toastApiError } from "@/api/errorToast";
import { type RunWith, runWithBody } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useMarkPart, usePart, useSendParts } from "@/hooks/useMaps";
import { WORK_WORDS, workState } from "@/lib/mapView";
import { formatDateTime } from "@/lib/utils";

const VERDICTS: { id: Verdict; label: string }[] = [
  { id: "bloat", label: "Bloat" },
  { id: "keep", label: "Keep" },
  { id: "unsure", label: "Not sure" },
];

const SEND_KINDS: { id: SendKind; label: string; says: string }[] = [
  { id: "remove", label: "Remove", says: "An agent deletes it and what only it uses, in a pull request. Your bloat mark is the decision; it does not re-assess it." },
  { id: "fix", label: "Fix", says: "An agent makes the smallest fix the evidence calls for, in a pull request." },
  { id: "analysis", label: "Analysis", says: "An agent reads the code and reports what it does and what to do; it changes nothing." },
];

const STATE_TONE = { none: "neutral", running: "info", pr: "gold", merged: "success", done: "success", failed: "error" } as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5 border-t border-border pt-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{title}</h3>
      {children}
    </section>
  );
}

interface PartPanelProps {
  slug: string;
  nodeId: string;
  by: string;
  canSend: boolean;
  onClose: () => void;
}

/** Knowledge maps (spec 2026-10-27): one part — what the map knows, everyone's marks, and sending it to an agent. */
export function PartPanel({ slug, nodeId, by, canSend, onClose }: PartPanelProps) {
  const part = usePart(slug, nodeId);
  const mark = useMarkPart(slug);
  const send = useSendParts(slug);
  const mine = part.data?.marks.find((m) => m.by.toLowerCase() === by.toLowerCase());
  const [note, setNote] = useState("");
  const [kind, setKind] = useState<SendKind>("analysis");
  const [runWith, setRunWith] = useState<RunWith>({});
  const [sendNote, setSendNote] = useState("");

  useEffect(() => {
    setNote(mine?.note ?? "");
    setKind(mine?.verdict === "bloat" ? "remove" : "analysis");
  }, [nodeId, mine?.note, mine?.verdict]);

  if (part.isLoading) return <Skeleton className="h-64 w-full" />;
  if (part.isError || !part.data) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-fg-muted">This part is not on the map any more.</p>
        <Button size="sm" variant="outline" onClick={onClose}>Close</Button>
      </div>
    );
  }
  const { node, detail, marks, work, lead, repo } = part.data as PartView;
  const others = marks.filter((m) => m !== mine);
  const state = workState(work);
  const bloat = detail?.bloat ?? (node.bloat ? { score: node.bloat.score, band: node.bloat.band } : undefined);

  const doMark = (verdict: Verdict | "clear") =>
    mark.mutate(
      { node_id: nodeId, verdict: verdict === mine?.verdict ? "clear" : verdict, note, by },
      {
        onSuccess: (r) =>
          toast.success(r.lead_id ? "Marked bloat: offered to the owners as a lead" : verdict === mine?.verdict ? "Mark taken back" : "Marked"),
        onError: (e: Error) => toastApiError(e, "The mark was not saved"),
      },
    );

  const doSend = () =>
    send.mutate(
      { node_ids: [nodeId], kind, note: sendNote, by, ...runWithBody(runWith) },
      {
        onSuccess: (r) => {
          const one = r.results[0];
          if (one?.job_id) toast.success(`Sent: ${one.job_id}`);
          else toast.error(`Not sent: ${one?.error ?? "unknown"}`);
        },
        onError: (e: Error) => toastApiError(e, "Not sent"),
      },
    );

  return (
    <div className="space-y-3 text-sm" data-testid="part-panel">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="break-words text-base font-semibold text-fg">{node.label}</h2>
          <p className="text-xs text-fg-muted">
            {node.kind}
            {node.area ? ` · ${node.area}` : ""}
            {node.sub ? ` · ${node.sub}` : ""}
          </p>
          {node.path && (
            <p className="mt-1 break-all font-mono text-xs text-fg-secondary">
              {node.url ? (
                <a className="underline decoration-dotted hover:text-fg" href={node.url} target="_blank" rel="noreferrer">
                  {node.path}
                  {node.line ? `:${node.line}` : ""}
                </a>
              ) : (
                node.path
              )}
            </p>
          )}
        </div>
        <Button size="xs" variant="ghost" onClick={onClose} aria-label="Close the part">Close</Button>
      </div>

      {node.signals.length > 0 && (
        <ul className="space-y-1">
          {node.signals.map((s) => (
            <li key={s} className="rounded border border-warning/30 bg-amber-dim px-2 py-1 text-xs text-fg">{s}</li>
          ))}
        </ul>
      )}

      {bloat && (
        <Section title="Bloat score">
          <p>
            <span className="font-semibold">{bloat.score}</span>/100 ·{" "}
            {bloat.band === "bloat" ? "bloat candidate" : bloat.band === "look" ? "look closer" : "likely needed"}
          </p>
          <ul className="space-y-0.5 text-xs text-fg-secondary">
            {(detail?.bloat?.against ?? []).map((r) => <li key={`a${r.text}`}>+{r.w} {r.text}</li>)}
            {(detail?.bloat?.for ?? []).map((r) => <li key={`f${r.text}`}>−{r.w} {r.text}</li>)}
            {(detail?.bloat?.risks ?? []).map((r) => <li key={`r${r}`}>Needs a person: {r}</li>)}
          </ul>
        </Section>
      )}

      {detail?.narrative && (
        <Section title="What it is">
          {detail.narrative.does && <p>{detail.narrative.does}</p>}
          {detail.narrative.why && <p className="text-fg-secondary"><span className="text-fg-muted">Why: </span>{detail.narrative.why}</p>}
          {detail.narrative.useful && <p className="text-fg-secondary"><span className="text-fg-muted">For keeping: </span>{detail.narrative.useful}</p>}
          {detail.narrative.against && <p className="text-fg-secondary"><span className="text-fg-muted">Against: </span>{detail.narrative.against}</p>}
          {detail.narrative.lean && (
            <p className="text-xs text-fg-muted">The scanner's reading: {detail.narrative.lean} ({detail.narrative.sure ?? "?"} confidence)</p>
          )}
        </Section>
      )}

      {(detail?.use?.length ?? 0) > 0 && (
        <Section title="Use">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
            {detail?.use?.map((u) => (
              <div key={u.label} className="contents">
                <dt className="text-fg-muted">{u.label}</dt>
                <dd>{u.n != null ? `${u.n.toLocaleString()} ${u.unit ?? ""}` : (u.text ?? "not measured")}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      {detail?.history?.added && (
        <Section title="History">
          <p className="text-xs text-fg-secondary">
            Added {String(detail.history.added.date ?? "").slice(0, 10)} by {detail.history.added.author}
            {detail.history.added.subject ? ` ("${detail.history.added.subject}")` : ""}; {detail.history.changes ?? "?"} changes.
          </p>
          {(detail.history.prs ?? []).slice(0, 5).map((p) => (
            <p key={p.number} className="text-xs">
              {p.url ? <a className="underline decoration-dotted" href={p.url} target="_blank" rel="noreferrer">#{p.number}</a> : `#${p.number}`} {p.title}
            </p>
          ))}
        </Section>
      )}

      <Section title="Your mark">
        <div className="flex gap-1.5" role="group" aria-label="Your mark">
          {VERDICTS.map((v) => (
            <Button
              key={v.id}
              size="xs"
              variant={mine?.verdict === v.id ? "default" : "outline"}
              aria-pressed={mine?.verdict === v.id}
              disabled={mark.isPending}
              onClick={() => doMark(v.id)}
            >
              {v.label}
            </Button>
          ))}
        </div>
        <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Why? (saved with your mark)" aria-label="A note with your mark" />
        {others.length > 0 ? (
          <ul className="space-y-0.5 text-xs text-fg-secondary">
            {others.map((m: MarkView) => (
              <li key={m.by}>
                <span className="font-semibold">{m.verdict === "unsure" ? "not sure" : m.verdict}</span> {m.by}
                {m.at ? ` · ${formatDateTime(m.at)}` : ""}
                {m.note ? `: ${m.note}` : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-fg-muted">
            {repo ? "A bloat mark offers it to the owners of " + repo + " as a lead." : "This part is in no repository: a mark stays on the map."}
          </p>
        )}
        {lead && <p className="text-xs text-fg-muted">Lead: {lead.state === "open" ? "open" : "closed"}</p>}
      </Section>

      <Section title="Agent work">
        {work ? (
          <div className="space-y-1">
            <p className="flex flex-wrap items-center gap-2">
              <Badge tone={STATE_TONE[state]}>{WORK_WORDS[state]}</Badge>
              <span className="text-xs text-fg-secondary">
                {work.kind} · {work.by}
                {work.sent_at ? ` · ${formatDateTime(work.sent_at)}` : ""}
              </span>
            </p>
            {work.job_id && <p className="text-xs"><Link className="underline decoration-dotted" to={`/jobs/${work.job_id}`}>{work.job_id}</Link>{work.cost_usd != null ? ` · $${work.cost_usd.toFixed(2)}` : ""}</p>}
            {work.pr_url && <p className="text-xs"><a className="underline decoration-dotted" href={work.pr_url} target="_blank" rel="noreferrer">Pull request</a></p>}
            {work.error && <p className="text-xs text-destructive">{work.error}</p>}
          </div>
        ) : (
          <p className="text-xs text-fg-muted">Not sent yet.</p>
        )}
        {repo ? (
          <div className="space-y-2 pt-1">
            <div className="flex gap-1.5" role="group" aria-label="What the agent does">
              {SEND_KINDS.map((k) => (
                <Button key={k.id} size="xs" variant={kind === k.id ? "default" : "outline"} aria-pressed={kind === k.id} onClick={() => setKind(k.id)}>
                  {k.label}
                </Button>
              ))}
            </div>
            <p className="text-xs text-fg-muted">{SEND_KINDS.find((k) => k.id === kind)?.says}</p>
            <Input value={sendNote} onChange={(e) => setSendNote(e.target.value)} maxLength={1000} placeholder="A note for the agent (optional)" aria-label="A note for the agent" />
            <RunWithControl value={runWith} onChange={setRunWith} repo={repo} />
            <Button size="sm" disabled={!canSend || send.isPending} onClick={doSend}>
              {send.isPending ? "Sending…" : work ? "Send again" : "Send to an agent"}
            </Button>
            {!canSend && <p className="text-xs text-fg-muted">Only an admin sends work.</p>}
          </div>
        ) : (
          <p className="text-xs text-fg-muted">This part is in no repository, so no agent can take it.</p>
        )}
      </Section>
    </div>
  );
}

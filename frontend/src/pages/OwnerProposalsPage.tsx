import { useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import type { OwnerProposal, OwnerProposalKind } from "@/api/ownerProposals";
import { toastApiError } from "@/api/errorToast";
import { PageError } from "@/components/PageError";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useAnswerOwnerProposal, useOwnerProposals } from "@/hooks/useOwnerProposals";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";
import { formatDateTime } from "@/lib/utils";
import { type RunWith, runWithBody } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";

const KIND_LABEL: Record<OwnerProposalKind, string> = {
  case: "Watcher problem",
  issue: "Open issue",
  finding: "Context finding",
  ticket: "Backlog ticket",
  ci: "Failing CI",
  alert: "Alert",
  flaky: "Flaky check",
  review: "Unanswered review",
  crack: "Scout crack",
  weed: "Scout weed",
};

const SIZE_TONE = { small: "success", medium: "info", large: "warning" } as const;

/** The kinds the filter offers: the three owner kinds and the backlog tickets. */
const FILTER_KINDS = ["", "case", "issue", "finding", "ticket"] as const;

/** How long "Later" waits before asking again. */
const LATER_DAYS = 7;

function ProposalCard({
  proposal,
  canAnswer,
  by,
  runWith,
}: {
  proposal: OwnerProposal;
  canAnswer: boolean;
  by: string;
  runWith: RunWith;
}) {
  const answer = useAnswerOwnerProposal();
  const [open, setOpen] = useState(false);
  const send = (decision: "accept" | "decline" | "snooze", body: { reason?: string; until?: string } = {}) =>
    answer.mutate(
      // What a yes starts runs with the model and key chosen above the list.
      { id: proposal.id, decision, body: { by, ...body, ...(decision === "accept" ? runWithBody(runWith) : {}) } },
      {
        onSuccess: (done) =>
          toast.success(decision === "accept" ? `Started${done.job_id ? ` ${done.job_id}` : ""}` : decision === "decline" ? "Declined" : "Snoozed"),
        onError: (e: Error) => toastApiError(e, "Failed"),
      },
    );
  return (
    <article className="rounded-md border border-border bg-white/[0.02] p-3" data-testid="owner-proposal" data-kind={proposal.kind}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex min-w-0 items-baseline gap-2">
          <Badge tone="neutral">{KIND_LABEL[proposal.kind]}</Badge>
          <span className="truncate font-mono text-xs text-white/60">{proposal.repo}</span>
          {proposal.area && <Badge tone="purple" title="The part of the repository it is about">{proposal.area}</Badge>}
        </div>
        <span className="text-xs text-muted-foreground">
          {proposal.owners.length ? `Owners: ${proposal.owners.join(", ")}` : "No owners set"} · {formatDateTime(proposal.created_at)}
        </span>
      </div>
      {proposal.brief ? (
        <div className="mt-2 space-y-1 text-sm" data-testid="owner-proposal-brief">
          <p className="flex flex-wrap items-baseline gap-2 font-semibold text-white/95">
            {proposal.brief.title}
            <Badge tone={SIZE_TONE[proposal.brief.size]} title="How much work the fix is">
              {proposal.brief.size}
            </Badge>
          </p>
          <p className="text-white/85">
            <span className="font-medium text-white/60">Problem: </span>
            {proposal.brief.problem}
          </p>
          <p className="text-white/85">
            <span className="font-medium text-white/60">Fix: </span>
            {proposal.brief.fix}
          </p>
        </div>
      ) : (
        <p className="mt-2 text-sm text-white/90">{proposal.summary}</p>
      )}
      <button type="button" className="mt-1 text-xs text-white/50 underline hover:text-white" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide details" : "Details"}
      </button>
      {open && (
        <div className="mt-1 space-y-1 text-xs text-white/70" data-testid="owner-proposal-details">
          {proposal.brief && <p>{proposal.summary}</p>}
          {proposal.details && <pre className="whitespace-pre-wrap">{proposal.details}</pre>}
          <p className="font-mono text-white/40">{proposal.id}</p>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Yes: {proposal.action}</span>
        {proposal.url && (
          <a className="text-xs underline text-white/60 hover:text-white" href={proposal.url} target="_blank" rel="noreferrer">
            Open
          </a>
        )}
        {canAnswer && (
          <span className="ml-auto flex gap-2">
            <Button size="sm" disabled={answer.isPending} onClick={() => send("accept")}>
              Start
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={answer.isPending}
              onClick={() => {
                const reason = window.prompt("Why not? (kept with the answer)") ?? "";
                send("decline", { reason });
              }}
            >
              Not this
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => send("snooze", { until: new Date(Date.now() + LATER_DAYS * 86_400_000).toISOString() })}
            >
              Later
            </Button>
          </span>
        )}
      </div>
    </article>
  );
}

/** The proposals asked, then the held backlog tickets folded below them (proposal briefs, spec 2026-10-25). */
function ProposalList({
  proposals,
  owner,
  canAnswer,
  by,
  runWith,
}: {
  proposals: OwnerProposal[];
  owner: string;
  canAnswer: boolean;
  by: string;
  runWith: RunWith;
}) {
  const asked = proposals.filter((p) => !p.held);
  const held = proposals.filter((p) => p.held);
  return (
    <div className="space-y-2">
      {asked.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing to ask{owner ? ` ${owner}` : ""} right now.</p>
      ) : (
        asked.map((p) => <ProposalCard key={p.id} proposal={p} canAnswer={canAnswer} by={by} runWith={runWith} />)
      )}
      {held.length > 0 && (
        <details className="rounded-md border border-border p-2" data-testid="owner-proposals-held">
          <summary className="cursor-pointer text-sm text-fg-secondary">
            Not ready enough to ask ({held.length}): backlog tickets below the readiness floor
          </summary>
          <div className="mt-2 space-y-2">
            {held.map((p) => (
              <ProposalCard key={p.id} proposal={p} canAnswer={canAnswer} by={by} runWith={runWith} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

/**
 * Owner proposals (spec 2026-10-24): what embry0 asks a repository's owners to
 * start — problems the watcher keeps seeing, open issues nobody started, and
 * claims the context audit thinks are wrong. A chat assistant asks owners the
 * same through the MCP server; here an admin can read and answer them too.
 */
export function OwnerProposalsPage() {
  const [params, setParams] = useSearchParams();
  const owner = params.get("owner") ?? "";
  const kind = (params.get("kind") ?? "") as OwnerProposalKind | "";
  // Held backlog tickets come too: a chat does not ask them, this page shows them folded.
  const proposals = useOwnerProposals({ ...(owner ? { owner } : {}), ...(kind ? { kind } : {}), include_held: true });
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const [runWith, setRunWith] = useState<RunWith>({});
  // The keys offered are the ones the listed repository may use; across several, only those every one may.
  const repos = [...new Set((proposals.data ?? []).map((p) => p.repo))];
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-4" data-testid="owner-proposals-page">
      <header className="space-y-1">
        <h1 className="title-pixel text-xl font-bold">Owner proposals</h1>
        <p className="text-sm text-fg-secondary">
          What embry0 asks each repository&apos;s owners to start. Owners come from the repository&apos;s settings, else its CODEOWNERS.
        </p>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="Owner"
          className="w-56"
          placeholder="GitHub login"
          value={owner}
          onChange={(e) => setParam("owner", e.target.value.trim())}
        />
        {FILTER_KINDS.map((k) => (
          <Button key={k || "all"} size="sm" variant={kind === k ? "default" : "ghost"} onClick={() => setParam("kind", k)}>
            {k ? KIND_LABEL[k] : "All"}
          </Button>
        ))}
      </div>
      {isAdmin && <RunWithControl value={runWith} onChange={setRunWith} repo={repos.length === 1 ? repos[0] : null} />}
      {proposals.isError ? (
        <PageError message="Proposals did not load" onRetry={() => void proposals.refetch()} />
      ) : !proposals.data ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <ProposalList proposals={proposals.data} owner={owner} canAnswer={isAdmin} by={by} runWith={runWith} />
      )}
    </div>
  );
}

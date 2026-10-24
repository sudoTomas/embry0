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

const KIND_LABEL: Record<OwnerProposalKind, string> = {
  case: "Watcher problem",
  issue: "Open issue",
  finding: "Context finding",
};

/** How long "Later" waits before asking again. */
const LATER_DAYS = 7;

function ProposalCard({ proposal, canAnswer, by }: { proposal: OwnerProposal; canAnswer: boolean; by: string }) {
  const answer = useAnswerOwnerProposal();
  const [open, setOpen] = useState(false);
  const send = (decision: "accept" | "decline" | "snooze", body: { reason?: string; until?: string } = {}) =>
    answer.mutate(
      { id: proposal.id, decision, body: { by, ...body } },
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
        </div>
        <span className="text-xs text-muted-foreground">
          {proposal.owners.length ? `Owners: ${proposal.owners.join(", ")}` : "No owners set"} · {formatDateTime(proposal.created_at)}
        </span>
      </div>
      <p className="mt-2 text-sm text-white/90">{proposal.summary}</p>
      {proposal.details && (
        <button type="button" className="mt-1 text-xs text-white/50 underline hover:text-white" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Details"}
        </button>
      )}
      {open && <pre className="mt-1 whitespace-pre-wrap text-xs text-white/70" data-testid="owner-proposal-details">{proposal.details}</pre>}
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
  const proposals = useOwnerProposals({ ...(owner ? { owner } : {}), ...(kind ? { kind } : {}) });
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
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
        {(["", "case", "issue", "finding"] as const).map((k) => (
          <Button key={k || "all"} size="sm" variant={kind === k ? "default" : "ghost"} onClick={() => setParam("kind", k)}>
            {k ? KIND_LABEL[k] : "All"}
          </Button>
        ))}
      </div>
      {proposals.isError ? (
        <PageError message="Proposals did not load" onRetry={() => void proposals.refetch()} />
      ) : !proposals.data ? (
        <Skeleton className="h-40 w-full" />
      ) : proposals.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing to ask{owner ? ` ${owner}` : ""} right now.</p>
      ) : (
        <div className="space-y-2">
          {proposals.data.map((p) => (
            <ProposalCard key={p.id} proposal={p} canAnswer={isAdmin} by={by} />
          ))}
        </div>
      )}
    </div>
  );
}

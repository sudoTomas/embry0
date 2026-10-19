import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { apiErrorMessage, formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { QaCheckResult, QaCheckWarning, SetupProposal } from "@/api/repoSetup";
import { useApplyProposal, useLatestProposal, useRejectProposal } from "@/hooks/useSetupProposal";
import { toastWithUndo } from "@/hooks/useRepoHistory";
import { IssueBody } from "@/components/issues/IssueBody";
import { Button } from "@/components/ui/Button";

const HEADING = "embry0 has a test setup for you to review. Nothing has changed yet.";
const STALE =
  "Someone changed the test setup after embry0 made this proposal. Run embry0 again for a fresh one.";
const APPLIED =
  "Testing is set up. It applies from the next test run, including jobs already running that have not reached their test step.";

const warningKey = (w: QaCheckWarning) => `${w.code}:${w.plain}`;

/**
 * Why the server refused an apply, in words the person can act on. A 409
 * (the setup changed since, or the proposal was decided elsewhere) blocks
 * Apply; a 422 explains and lets the person tick again. Null for any other
 * failure, which is toasted.
 */
function refusalOf(error: unknown): { text: string; blocks: boolean } | null {
  const response = (error as { response?: { status?: unknown; data?: { detail?: unknown } } } | null)?.response;
  const detail = response?.data?.detail;
  if (response?.status === 409) return { text: formatErrorDetail(detail) ?? STALE, blocks: true };
  if (response?.status !== 422) return null;
  if (detail && typeof detail === "object" && Array.isArray((detail as { missing?: unknown }).missing)) {
    return { text: "Tick each warning shown before you apply.", blocks: false };
  }
  const problems = Array.isArray(detail) ? detail : (detail as { problems?: unknown } | null)?.problems;
  const plain = Array.isArray(problems)
    ? problems.map((p) => (p as { plain?: unknown } | null)?.plain).filter((p): p is string => typeof p === "string")
    : [];
  return { text: plain.length > 0 ? plain.join(" ") : apiErrorMessage(error), blocks: false };
}

function Disclosure({ summary, children }: { summary: string; children: ReactNode }) {
  return (
    <details className="rounded-md border border-border px-3 py-2 text-sm">
      <summary className="cursor-pointer text-fg-secondary hover:text-fg">{summary}</summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

/** What the setup starts, where it tests and what it checks, from the free check's summary. */
function SetupSummary({ check }: { check: QaCheckResult }) {
  const apps = check.summary?.apps ?? [];
  const many = apps.length > 1;
  const criteria = apps.flatMap((app) => app.criteria.map((c) => ({ app: app.name, text: c })));
  return (
    <>
      {apps.length > 0 && (
        <ul className="space-y-1 text-sm text-fg">
          {apps.map((app) => {
            const where = `${app.address}${app.address_is_local ? " on embry0's test computer" : ""}`;
            return (
              <li key={app.name}>
                {app.start === null ? (
                  <>Tests {app.name} at {where}.</>
                ) : (
                  <>
                    Starts {app.name} with{" "}
                    <code className="rounded bg-overlay px-1 py-0.5 font-mono text-xs">{app.start}</code>. Tests it at{" "}
                    {where}.
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {criteria.length > 0 && (
        <div className="space-y-1 text-sm">
          <p className="text-fg">After each change it will check:</p>
          <ul className="list-disc space-y-0.5 pl-5 text-fg-secondary" data-testid="setup-proposal-criteria">
            {criteria.map((c, i) => (
              <li key={i}>
                {many ? `${c.app}: ` : ""}
                {c.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

/** Only what the boot and ready-check run proved, apart from anything a person is asked to accept. */
function Proven({ proposal }: { proposal: SetupProposal }) {
  const apps = proposal.check?.summary?.apps ?? [];
  const passed = Object.entries(proposal.smoke?.results ?? {})
    .filter(([, outcome]) => outcome === "passed")
    .map(([name]) => name);
  if (passed.length === 0) return null;
  return (
    <div className="space-y-1 text-sm" data-testid="setup-proposal-proven">
      <p className="text-fg">What embry0 proved:</p>
      <ul className="list-disc space-y-0.5 pl-5 text-fg-secondary">
        {passed.map((name) => (
          <li key={name}>
            {apps.find((app) => app.name === name)?.target === "deployed"
              ? `embry0 checked ${name} where it already runs, and the check address answered.`
              : `embry0 started ${name} on its test computer and the check address answered.`}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface SetupProposalCardProps {
  repo: string;
  /** Collapse the card ('Not now', or after a decision). */
  onClose: () => void;
  /** Open the consent dialog for a fresh run. */
  onTryAgain: () => void;
  onSkip: () => void;
  skipping: boolean;
}

/**
 * The latest setup proposal, for a person to review in plain words and
 * apply or turn down. Nothing reaches the stored setup without Apply, and
 * Apply waits until every warning that asks for it is ticked.
 */
export function SetupProposalCard({ repo, onClose, onTryAgain, onSkip, skipping }: SetupProposalCardProps) {
  const qc = useQueryClient();
  const latest = useLatestProposal(repo);
  const apply = useApplyProposal(repo);
  const reject = useRejectProposal(repo);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [refusal, setRefusal] = useState<{ text: string; blocks: boolean } | null>(null);
  const proposal = latest.data;

  if (latest.isPending || latest.isError) {
    return (
      <div className="mt-3 rounded-md border border-border p-3" data-testid="setup-proposal" aria-busy={latest.isPending}>
        {latest.isError ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-fg-secondary">Could not load the setup proposal.</p>
            <Button variant="outline" size="sm" onClick={() => void latest.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <div className="h-10 w-full animate-pulse rounded bg-muted/60" />
        )}
      </div>
    );
  }

  if (proposal?.status === "failed") {
    return (
      <div className="mt-3 space-y-3 rounded-md border border-border p-3" data-testid="setup-proposal">
        <p className="whitespace-pre-wrap text-sm text-fg">embry0 could not set up testing: {proposal.failure}</p>
        {proposal.notes_md && (
          <Disclosure summary="What embry0 noticed">
            <IssueBody body={proposal.notes_md} />
          </Disclosure>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onTryAgain}>
            Try again
          </Button>
          <Button variant="outline" size="sm" disabled={skipping} onClick={onSkip}>
            Skip testing for now
          </Button>
        </div>
      </div>
    );
  }

  if (proposal?.status !== "pending" && proposal?.status !== "stale") {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-border p-3" data-testid="setup-proposal">
        <p className="text-sm text-fg-secondary">
          {proposal?.status === "running"
            ? "embry0 is still working on a test setup. You can leave this page."
            : "There is no test setup waiting for review."}
        </p>
        <Button variant="outline" size="sm" onClick={onClose}>
          Not now
        </Button>
      </div>
    );
  }

  const check = proposal.check;
  const toTick = (check?.warnings ?? []).filter((w) => w.confirm);
  const needsYou = (check?.warnings ?? []).filter((w) => !w.confirm);
  const allTicked = toTick.every((w) => ticked.has(warningKey(w)));
  const blocked = refusal?.blocks ? refusal.text : proposal.status === "stale" ? STALE : null;
  const notice = blocked ?? refusal?.text ?? null;
  const canApply = !blocked && allTicked && check?.ok !== false && !apply.isPending && !reject.isPending;

  function toggle(w: QaCheckWarning) {
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(warningKey(w))) next.delete(warningKey(w));
      else next.add(warningKey(w));
      return next;
    });
  }

  function onApply() {
    if (!proposal || !canApply) return;
    setRefusal(null);
    const acknowledged = [...new Set(toTick.map((w) => w.code))];
    apply.mutate(
      { id: proposal.id, acknowledged },
      {
        onSuccess: (result) => {
          toastWithUndo(APPLIED, repo, result.revision_id, qc);
          onClose();
        },
        onError: (error) => {
          const refused = refusalOf(error);
          if (refused) setRefusal(refused);
          else toastApiError(error, "Could not apply the setup");
        },
      },
    );
  }

  function onReject() {
    if (!proposal || reject.isPending) return;
    reject.mutate(proposal.id, {
      onSuccess: () => {
        toast.success("Setup turned down. Nothing was changed.");
        onClose();
      },
      onError: (error) => toastApiError(error, "Could not turn the setup down"),
    });
  }

  return (
    <div className="mt-3 space-y-3 rounded-md border border-border p-3" data-testid="setup-proposal">
      <h3 className="text-sm font-semibold text-fg">{HEADING}</h3>

      {check && check.ok === false && (
        <div className="space-y-1 text-sm">
          <p className="text-fg">This setup would stop every test run, so it cannot be applied:</p>
          <ul className="list-disc space-y-0.5 pl-5 text-destructive">
            {check.problems.map((p, i) => (
              <li key={i}>
                {p.plain} {p.fix}
              </li>
            ))}
          </ul>
        </div>
      )}

      {check && <SetupSummary check={check} />}
      <Proven proposal={proposal} />

      {toTick.length > 0 && (
        <fieldset className="space-y-2 rounded-md border border-warning/30 bg-amber-dim p-3 text-sm">
          <legend className="px-1 text-fg">Before you apply, tick each of these:</legend>
          {toTick.map((w) => (
            <label key={warningKey(w)} className="flex items-start gap-2 text-fg">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 rounded border-input"
                checked={ticked.has(warningKey(w))}
                onChange={() => toggle(w)}
              />
              <span>{w.plain}</span>
            </label>
          ))}
        </fieldset>
      )}

      {needsYou.length > 0 && (
        <div className="space-y-1 text-sm" data-testid="setup-proposal-needs-you">
          <p className="text-fg">Still needs you</p>
          <ul className="list-disc space-y-0.5 pl-5 text-fg-secondary">
            {needsYou.map((w) => (
              <li key={warningKey(w)}>
                {w.plain}
                {w.code === "login_value_missing" && (
                  <>
                    {" "}
                    <Link
                      to={`/environments?repo=${encodeURIComponent(repo)}`}
                      className="text-fg underline underline-offset-2 hover:text-fg-secondary"
                    >
                      Add it
                    </Link>
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {proposal.notes_md && (
        <Disclosure summary="Why embry0 chose this">
          <IssueBody body={proposal.notes_md} />
        </Disclosure>
      )}

      <Disclosure summary="Show the technical version">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="min-w-0 space-y-1">
            <p className="text-xs text-fg-muted">Now</p>
            {proposal.current_yaml ? (
              <pre className="overflow-x-auto rounded-md bg-overlay p-2 font-mono text-xs" data-testid="setup-proposal-current">
                {proposal.current_yaml}
              </pre>
            ) : (
              <p className="text-xs text-fg-secondary">No test setup yet</p>
            )}
          </div>
          <div className="min-w-0 space-y-1">
            <p className="text-xs text-fg-muted">Proposed</p>
            <pre className="overflow-x-auto rounded-md bg-overlay p-2 font-mono text-xs" data-testid="setup-proposal-proposed">
              {proposal.proposed_yaml ?? ""}
            </pre>
          </div>
        </div>
      </Disclosure>

      {notice && (
        <p role="alert" className="text-sm text-destructive">
          {notice}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!canApply} onClick={onApply}>
          Apply this setup
        </Button>
        <Button variant="outline" size="sm" onClick={onClose}>
          Not now
        </Button>
        {!blocked && (
          <Button variant="ghost" size="sm" disabled={reject.isPending || apply.isPending} onClick={onReject}>
            Turn this down
          </Button>
        )}
      </div>
    </div>
  );
}

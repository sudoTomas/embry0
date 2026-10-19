import { Fragment, useLayoutEffect, useRef } from "react";
import { Link } from "react-router";
import { format } from "date-fns";
import { useQueue } from "@/hooks/useQueue";
import { useProposals } from "@/hooks/useProposals";
import { usePendingMergeApprovals } from "@/hooks/useMergeApprovals";
import { usePendingSetupProposals } from "@/hooks/useSetupProposal";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import { hasAnswered, useFirstPaint } from "@/hooks/useFirstPaint";
import { cn } from "@/lib/utils";

interface BriefItem {
  key: string;
  count: number;
  text: string;
  /** Where the number is answered; the merge asks are answered in the approval chat. */
  to?: string;
  /** Waits for a person: only shown above zero. */
  needsPerson: boolean;
}

/** What waits for a person reads in the body colour, never amber or gold:
 * the page keeps its one gold for the Needs-you count. Blue marks only live
 * work above zero, so an idle "0 running" reads muted. Every linked item
 * turns gold on hover. */
function itemTone(item: BriefItem): string {
  if (item.needsPerson) return "text-fg";
  return item.count > 0 ? "text-info" : "text-fg-muted";
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

/**
 * The Overview's one-line brief, under the header like the sibling
 * dashboards': today's date, what waits for a person, and what is running,
 * each number bold and linked to the page that answers it. Every number comes
 * from a poll the dashboard already runs (queue, proposals), the merge-ask
 * list or the test setups waiting for review; a source that is off or failing
 * leaves its item out. Below the sm breakpoint the items wrap onto a second
 * line instead of scrolling out of sight.
 *
 * The items wait for all four sources' first answers and arrive together:
 * drawn as each answer landed, the line wrapped onto one more line after
 * another on a phone. `onReady` tells the layout when they are in.
 */
export function BriefBar({ onReady }: { onReady?: (ready: boolean) => void }) {
  const queue = useQueue();
  const proposals = useProposals();
  const approvals = usePendingMergeApprovals();
  const setupProposals = usePendingSetupProposals();

  const cases = proposals.data?.cases ?? [];
  const orgNeeds = cases.filter((c) => c.origin === "org").length;
  const drafts = cases.length - orgNeeds;
  const awaiting = queue.data?.awaiting_input ?? 0;
  const paused = queue.data?.paused ?? 0;
  const asks = approvals.data?.length ?? 0;
  const setups = setupProposals.data ?? [];

  const items: BriefItem[] = [
    { key: "awaiting", count: awaiting, text: "awaiting input", to: "/console", needsPerson: true },
    { key: "paused", count: paused, text: "paused", to: "/console", needsPerson: true },
    { key: "approvals", count: asks, text: plural(asks, "merge approval asked", "merge approvals asked"), needsPerson: true },
    { key: "org-needs", count: orgNeeds, text: plural(orgNeeds, "organization need", "organization needs"), to: "/orgs", needsPerson: true },
    { key: "drafts", count: drafts, text: plural(drafts, "draft ticket to decide", "draft tickets to decide"), to: "/issues", needsPerson: true },
    {
      key: "setups",
      count: setups.length,
      text: plural(setups.length, "test setup to review", "test setups to review"),
      // One setup opens its repository's page, where the review card is; several open the list.
      to: setups.length === 1 ? `/repos/${setups[0].repo}` : "/repos",
      needsPerson: true,
    },
  ].filter((item) => item.count > 0);

  if (queue.data) {
    items.push(
      { key: "running", count: queue.data.running, text: "running", to: "/jobs", needsPerson: false },
      { key: "queued", count: queue.data.pending, text: "queued", to: "/jobs", needsPerson: false },
    );
  }

  const ready = useFirstPaint([queue, proposals, approvals, setupProposals].every(hasAnswered));
  // Before paint, so the layout reveals the page in the frame the items arrive.
  useLayoutEffect(() => {
    onReady?.(ready);
  }, [ready, onReady]);
  const nobodyNeeded = ready && !items.some((item) => item.needsPerson);

  return (
    <div
      data-testid="brief-bar"
      className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-[var(--density-padding)] py-1.5 text-sm sm:h-9 sm:flex-nowrap sm:gap-x-0 sm:overflow-x-auto sm:py-0 sm:[scrollbar-width:none]"
    >
      <span className="whitespace-nowrap text-fg-secondary">{format(new Date(), "EEEE, MMMM d")}</span>
      {nobodyNeeded && (
        <>
          <Divider />
          <span className="whitespace-nowrap text-fg-muted">Nothing waits for you</span>
        </>
      )}
      {ready && items.map((item) => {
        const body = (
          <>
            <BriefCount count={item.count} /> {item.text}
          </>
        );
        const tone = cn(itemTone(item), "whitespace-nowrap");
        return (
          <Fragment key={item.key}>
            <Divider />
            {item.to ? (
              <Link
                to={item.to}
                data-testid={`brief-${item.key}`}
                className={cn(tone, "transition-colors hover:text-primary")}
              >
                {body}
              </Link>
            ) : (
              <span data-testid={`brief-${item.key}`} className={tone} title="Answered in the approval chat">
                {body}
              </span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

/** One bold number of the brief. The first paint shows it as it is; when a
 * poll changes it, it travels to the new value and bumps once, so the change
 * is seen. Inline-block, because a transform does not apply to an inline
 * box. */
function BriefCount({ count }: { count: number }) {
  const ref = useRef<HTMLElement>(null);
  const shown = useTweenedNumber(count, { tickRef: ref });
  return (
    <b ref={ref} className="inline-block font-bold tabular-nums">
      {Math.round(shown ?? count)}
    </b>
  );
}

function Divider() {
  return <span aria-hidden="true" className="mx-4 hidden h-4 w-px shrink-0 bg-border-strong sm:block" />;
}

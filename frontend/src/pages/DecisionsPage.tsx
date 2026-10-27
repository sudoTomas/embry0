import { useState } from "react";
import { useSearchParams } from "react-router";
import type { Decision } from "@/api/decisions";
import type { RunWith } from "@/api/runWith";
import { DecisionCard } from "@/components/decisions/DecisionCard";
import { PageError } from "@/components/PageError";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useDecisions } from "@/hooks/useDecisions";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";

type Stage = "done" | "offered";

const STAGES: { stage: Stage; label: string; empty: string }[] = [
  { stage: "done", label: "Quests to review", empty: "Nothing waits for a review." },
  { stage: "offered", label: "Quests on offer", empty: "No quest on offer right now." },
];

/** Hand-offs come with the next step of the design; until then the cards do not offer them. */
const NOT_YET = ["send_to"] as const;

function CardList({ decisions, canAnswer, by, runWith }: { decisions: Decision[]; canAnswer: boolean; by: string; runWith: RunWith }) {
  const asked = decisions.filter((d) => !d.held);
  const held = decisions.filter((d) => d.held);
  return (
    <div className="space-y-2">
      {asked.map((d) => (
        <DecisionCard key={d.id} decision={d} canAnswer={canAnswer} by={by} runWith={runWith} hide={NOT_YET} />
      ))}
      {held.length > 0 && (
        <details className="rounded-md border border-border p-2" data-testid="decisions-held">
          <summary className="cursor-pointer text-sm text-fg-secondary">
            Not asked in chat ({held.length}): tickets below the readiness floor, weeds and minor cracks
          </summary>
          <div className="mt-2 space-y-2">
            {held.map((d) => (
              <DecisionCard key={d.id} decision={d} canAnswer={canAnswer} by={by} runWith={runWith} hide={NOT_YET} />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

/**
 * Decisions (spec 2026-10-27): every quest waiting on a person, as one card
 * each. Quests to review are finished jobs waiting for a merge, a send-back or
 * a rating; quests on offer are what embry0 found and asks owners to start.
 * The worst come first.
 */
export function DecisionsPage() {
  const [params, setParams] = useSearchParams();
  const stage: Stage = params.get("stage") === "offered" ? "offered" : "done";
  const owner = params.get("owner") ?? "";
  const kind = params.get("kind") ?? "";
  const decisions = useDecisions({ stage: "all", ...(owner ? { owner } : {}) });
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const [runWith, setRunWith] = useState<RunWith>({});
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "stage") next.delete("kind");
    setParams(next, { replace: true });
  };

  const all = decisions.data ?? [];
  const inStage = all.filter((d) => d.stage === stage);
  // Finished work all reads "Quest done": the kinds filter quests on offer only.
  const kinds = stage === "offered" ? [...new Map(inStage.map((d) => [d.kind, d.kind_label])).entries()] : [];
  const shown = kind ? inStage.filter((d) => d.kind === kind) : inStage;
  const repos = [...new Set(shown.map((d) => d.repo))];
  const current = STAGES.find((s) => s.stage === stage) ?? STAGES[0];

  return (
    <div className="space-y-4" data-testid="decisions-page">
      <header className="space-y-1">
        <h1 className="title-pixel text-xl font-bold">Decisions</h1>
        <p className="text-sm text-fg-secondary">
          Every quest waiting on someone: finished work to review, and work embry0 found that its owners could start. The worst come first.
        </p>
      </header>
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Stage">
        {STAGES.map((s) => (
          <Button
            key={s.stage}
            role="tab"
            aria-selected={stage === s.stage}
            size="sm"
            variant={stage === s.stage ? "default" : "ghost"}
            onClick={() => setParam("stage", s.stage === "done" ? "" : s.stage)}
          >
            {s.label}
            {decisions.data && <span className="ml-1.5 opacity-70">{all.filter((d) => d.stage === s.stage).length}</span>}
          </Button>
        ))}
        {stage === "offered" && (
          <Input
            aria-label="Owner"
            className="ml-auto w-56"
            placeholder="GitHub login"
            value={owner}
            onChange={(e) => setParam("owner", e.target.value.trim())}
          />
        )}
      </div>
      {kinds.length > 1 && (
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Kind">
          <Button size="xs" variant={kind ? "ghost" : "secondary"} onClick={() => setParam("kind", "")}>
            All
          </Button>
          {kinds.map(([k, label]) => (
            <Button key={k} size="xs" variant={kind === k ? "secondary" : "ghost"} onClick={() => setParam("kind", k)}>
              {label}
            </Button>
          ))}
        </div>
      )}
      {isAdmin && <RunWithControl value={runWith} onChange={setRunWith} repo={repos.length === 1 ? repos[0] : null} />}
      {decisions.isError ? (
        <PageError message="Decisions did not load" onRetry={() => void decisions.refetch()} />
      ) : !decisions.data ? (
        <Skeleton className="h-40 w-full" />
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">{current.empty}</p>
      ) : (
        <CardList decisions={shown} canAnswer={isAdmin} by={by} runWith={runWith} />
      )}
    </div>
  );
}

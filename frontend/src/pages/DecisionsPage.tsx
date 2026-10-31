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
import { useDecisions, usePeople, useSetMyGithubLogin } from "@/hooks/useDecisions";
import { useLeadKinds } from "@/hooks/useLeadKinds";
import { useUiSettings } from "@/hooks/useUiSettings";
import { toastApiError } from "@/api/errorToast";
import { toast } from "sonner";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";

type Stage = "mine" | "done" | "offered";

interface StageTab {
  stage: Stage;
  label: string;
  empty: string;
}

/** The page's words: the game's (a quest) while the game layer is on, plain ones while the
 * settings document turns it off (`ui.game`, #177). */
const WORDS: Record<"game" | "plain", { intro: string; stages: StageTab[] }> = {
  game: {
    intro: "Every quest waiting on someone: finished work to review, and work embry0 found that its owners could start. The worst come first.",
    stages: [
      { stage: "mine", label: "Waiting for me", empty: "Nothing waits for you: nothing was sent to you, and no quest on offer is owned by your GitHub login." },
      { stage: "done", label: "Quests to review", empty: "Nothing waits for a review." },
      { stage: "offered", label: "Quests on offer", empty: "No quest on offer right now." },
    ],
  },
  plain: {
    intro: "Everything waiting on someone: finished work to review, and work embry0 found that its owners could start. The worst come first.",
    stages: [
      { stage: "mine", label: "Waiting for me", empty: "Nothing waits for you: nothing was sent to you, and no work on offer is owned by your GitHub login." },
      { stage: "done", label: "Jobs to review", empty: "Nothing waits for a review." },
      { stage: "offered", label: "Work on offer", empty: "No work on offer right now." },
    ],
  },
};

/** Name your GitHub login once, so the quests your login owns wait for you here. */
function LinkLogin() {
  const set = useSetMyGithubLogin();
  const [login, setLogin] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-white/[0.02] p-2 text-sm"
      data-testid="link-github-login"
      onSubmit={(e) => {
        e.preventDefault();
        set.mutate(login.trim(), {
          onSuccess: (me) => toast.success(`You are @${me.github_login}`),
          onError: (err: Error) => toastApiError(err, "Not linked"),
        });
      }}
    >
      <span className="text-fg-secondary">Owners are named by GitHub login. Which one is yours?</span>
      <Input aria-label="Your GitHub login" className="h-8 w-44" placeholder="octocat" value={login} onChange={(e) => setLogin(e.target.value)} />
      <Button size="sm" type="submit" disabled={!login.trim() || set.isPending}>
        Link
      </Button>
    </form>
  );
}

function CardList({
  decisions,
  canAnswer,
  by,
  runWith,
  me,
}: {
  decisions: Decision[];
  canAnswer: boolean;
  by: string;
  runWith: RunWith;
  me: string;
}) {
  const asked = decisions.filter((d) => !d.held);
  const held = decisions.filter((d) => d.held);
  return (
    <div className="space-y-2">
      {asked.map((d) => (
        <DecisionCard key={d.id} decision={d} canAnswer={canAnswer} by={by} runWith={runWith} me={me} />
      ))}
      {held.length > 0 && (
        <details className="rounded-md border border-border p-2" data-testid="decisions-held">
          <summary className="cursor-pointer text-sm text-fg-secondary">
            Not asked in chat ({held.length}): tickets below the readiness floor, and leads of a severity the leads policy does not ask
          </summary>
          <div className="mt-2 space-y-2">
            {held.map((d) => (
              <DecisionCard key={d.id} decision={d} canAnswer={canAnswer} by={by} runWith={runWith} me={me} />
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
 * The worst come first. While the game layer is off the page says jobs and
 * work instead of quests.
 */
export function DecisionsPage() {
  const [params, setParams] = useSearchParams();
  const words = WORDS[useUiSettings().game ? "game" : "plain"];
  const raw = params.get("stage");
  const stage: Stage = raw === "offered" || raw === "done" ? raw : "mine";
  const owner = params.get("owner") ?? "";
  const kind = params.get("kind") ?? "";
  const decisions = useDecisions({ stage: "all", ...(owner ? { owner } : {}) });
  const waiting = useDecisions({ stage: "all", for: "me" });
  // The kinds filter names kinds as the quest board's does: the catalogue's plural (GET /lead-kinds, #166).
  const kindNames = useLeadKinds();
  const people = usePeople();
  const isAdmin = useIsAdmin();
  const by = useAuthStore((s) => s.user?.email) ?? "dashboard";
  const me = (useAuthStore((s) => s.user?.email) ?? "").toLowerCase();
  const myLogin = (people.data ?? []).find((p) => p.email.toLowerCase() === me)?.github_login;
  const [runWith, setRunWith] = useState<RunWith>({});
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "stage") next.delete("kind");
    setParams(next, { replace: true });
  };

  const all = decisions.data ?? [];
  const source = stage === "mine" ? waiting : decisions;
  const inStage = stage === "mine" ? (waiting.data ?? []) : all.filter((d) => d.stage === stage);
  // Finished work all reads one label ("Quest done", "Job done" with the game off): the kinds filter what is on offer only.
  const kinds =
    stage === "offered"
      ? [...new Map(inStage.map((d) => [d.kind, kindNames.get(d.kind)?.plural ?? d.kind_label])).entries()]
      : [];
  const shown = kind ? inStage.filter((d) => d.kind === kind) : inStage;
  const repos = [...new Set(shown.map((d) => d.repo))];
  const current = words.stages.find((s) => s.stage === stage) ?? words.stages[0];

  return (
    <div className="space-y-4" data-testid="decisions-page">
      <header className="space-y-1">
        <h1 className="title-page text-xl font-bold">Decisions</h1>
        <p className="text-sm text-fg-secondary">{words.intro}</p>
      </header>
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Stage">
        {words.stages.map((s) => (
          <Button
            key={s.stage}
            role="tab"
            aria-selected={stage === s.stage}
            size="sm"
            variant={stage === s.stage ? "default" : "ghost"}
            onClick={() => setParam("stage", s.stage === "mine" ? "" : s.stage)}
          >
            {s.label}
            {s.stage === "mine"
              ? waiting.data && <span className="ml-1.5 opacity-70">{waiting.data.length}</span>
              : decisions.data && <span className="ml-1.5 opacity-70">{all.filter((d) => d.stage === s.stage).length}</span>}
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
      {stage === "mine" && me && people.data && !myLogin && <LinkLogin />}
      {isAdmin && <RunWithControl value={runWith} onChange={setRunWith} repo={repos.length === 1 ? repos[0] : null} />}
      {source.isError ? (
        <PageError message="Decisions did not load" onRetry={() => void source.refetch()} />
      ) : !source.data ? (
        <Skeleton className="h-40 w-full" />
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">{current.empty}</p>
      ) : (
        <CardList decisions={shown} canAnswer={isAdmin} by={by} runWith={runWith} me={me} />
      )}
    </div>
  );
}

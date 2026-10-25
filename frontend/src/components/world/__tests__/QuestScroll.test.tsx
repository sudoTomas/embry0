import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { QuestScroll } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";
import { formatDateTime } from "@/lib/utils";

const state: { quest: QuestScroll | undefined } = { quest: undefined };
const mutation = () => ({ mutate: vi.fn(), isPending: false });

vi.mock("@/hooks/useWorld", () => ({
  useQuest: () => ({ data: state.quest, isError: false }),
  useRateQuest: () => mutation(),
  useSendBack: () => mutation(),
  useRejectQuest: () => mutation(),
  useApproveMerge: () => mutation(),
}));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
}));

import { QuestScrollDialog } from "../QuestScroll";

const SCROLL = {
  job_id: "job-1",
  repo: "acme/web",
  area: null,
  title: "Add the export",
  task: "Add the export",
  status: "completed",
  outcome: "pr_open",
  template: null,
  lead_id: null,
  report: "Added the export.",
  report_truncated: false,
  failure: null,
  error_code: null,
  pr_url: null,
  pr: null,
  merge_approval: null,
  verification: null,
  party: [{ agent_type: "developer", runs: 2, cost_usd: 2, seconds: 60, failed: 0 }],
  xp: [],
  cost_usd: 2.5,
  seconds: 3600,
  started_at: null,
  finished_at: null,
  evaluation: null,
} as unknown as QuestScroll;

function renderScroll(quest: QuestScroll) {
  state.quest = quest;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <QuestScrollDialog jobId="job-1" skillName={(s) => s} canAct={false} onClose={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("QuestScroll fallback models", () => {
  it("names each run that ran on a fallback model, and why", () => {
    renderScroll({
      ...SCROLL,
      fallbacks: [
        {
          agent_type: "developer",
          model: "grok-4.7",
          instead_of: "claude-opus-5",
          provider: "anthropic",
          reason: "quota_exhausted",
          until: null,
          runs: 2,
        },
      ],
    });
    const list = screen.getByTestId("quest-fallbacks");
    expect(list).toHaveTextContent(
      `${identityOf("developer").displayName} ran on grok-4.7 instead of claude-opus-5 (2 runs): the Anthropic account was paused (quota exhausted).`,
    );
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
  });

  it("shows nothing when every run had its own model, or the backend sends none", () => {
    renderScroll({ ...SCROLL, fallbacks: [] });
    expect(screen.queryByTestId("quest-fallbacks")).toBeNull();
    renderScroll(SCROLL);
    expect(screen.queryByTestId("quest-fallbacks")).toBeNull();
  });
});

describe("QuestScroll account wait", () => {
  it("says what a paused quest waits for and when it goes on, in the viewer's time", () => {
    const until = "2026-10-26T00:00:00+00:00";
    renderScroll({
      ...SCROLL,
      status: "paused",
      account_wait: { provider: "anthropic", reason: "quota_exhausted", until, chosen: true },
    });
    const box = screen.getByTestId("quest-account-wait");
    expect(box).toHaveTextContent("the Anthropic account is quota exhausted");
    expect(box).toHaveTextContent(`It goes on at ${formatDateTime(until)}`);
    expect(box).toHaveTextContent("A person chose this model or key");
  });

  it("shows nothing for a quest that waits for nothing", () => {
    renderScroll({ ...SCROLL, account_wait: null });
    expect(screen.queryByTestId("quest-account-wait")).toBeNull();
  });
});

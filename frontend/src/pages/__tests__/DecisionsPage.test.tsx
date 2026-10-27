import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Decision } from "@/api/decisions";

const mockUseDecisions = vi.fn();
const mutate = vi.fn();
vi.mock("@/hooks/useDecisions", () => ({
  useDecisions: (filters: unknown) => mockUseDecisions(filters),
  useAnswerDecision: () => ({ mutate, isPending: false }),
}));
const admin = { current: true };
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => admin.current,
  useAuthStore: (select: (s: { user: { email: string } }) => unknown) => select({ user: { email: "owner@example.com" } }),
}));
vi.mock("@/hooks/useRunWith", () => ({ useRunWithKeys: () => ({ data: [] }) }));
vi.mock("@/hooks/useModels", () => ({ useModels: () => ({ models: [], providers: [], isLoading: false }) }));

import { DecisionsPage } from "../DecisionsPage";

const REVIEW: Decision = {
  id: "done:job-1",
  stage: "done",
  kind: "full-magnum-opus",
  kind_label: "Quest done",
  verdict: { label: "Ready to merge", tone: "good" },
  title: "Fix the leads crash loop",
  repo: "acme/web",
  area: "Leads",
  at: "2026-10-27T12:00:00+00:00",
  facts: [
    { label: "Pull request", value: "#44 · +212 −38 · 6 files", tone: "" },
    { label: "Checks", value: "passing", tone: "good" },
  ],
  why: "Users got 502s for two minutes.",
  yes_starts: null,
  actions: ["merge", "send_back", "reject", "run_again", "send_to"],
  owners: [],
  held: false,
  assigned_to: null,
  links: { job: "job-1", pr: "https://github.com/acme/web/pull/44", merge_approval: "ma-1" },
  details: "The report.",
};

const CRACK: Decision = {
  ...REVIEW,
  id: "offer:crack:1a2b",
  stage: "offered",
  kind: "crack",
  kind_label: "Crack",
  verdict: { label: "High", tone: "bad" },
  title: "A committed key bypasses row-level security",
  facts: [{ label: "Evidence", value: "lib/probe.mjs:4 (+1)", tone: "" }],
  why: "Anyone with the repository can read every row.",
  yes_starts: "a job that removes it and opens a pull request",
  actions: ["start", "decline", "later", "send_to"],
  owners: ["octocat"],
  links: { proposal: "crack:1a2b" },
};

const WEED: Decision = { ...CRACK, id: "offer:weed:9", kind: "weed", kind_label: "Weed", held: true, title: "Dead export" };

function renderPage(path = "/decisions") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DecisionsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mutate.mockReset();
  admin.current = true;
  mockUseDecisions.mockReturnValue({ data: [CRACK, REVIEW, WEED] });
});

describe("DecisionsPage (decisions, spec 2026-10-27)", () => {
  it("opens on the quests to review, each card reading its verdict, facts and why before anything else", () => {
    renderPage();
    const cards = screen.getAllByTestId("decision-card");
    expect(cards).toHaveLength(1);
    const card = within(cards[0]);
    expect(card.getByText("Ready to merge")).toBeInTheDocument();
    expect(card.getByText("#44 · +212 −38 · 6 files")).toBeInTheDocument();
    expect(card.getByText("Users got 502s for two minutes.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Quests to review/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /Quests on offer/ })).toHaveTextContent("2");
  });

  it("offers the likely answer first and keeps hand-offs back until they exist", () => {
    renderPage();
    const buttons = within(screen.getByTestId("decision-card")).getAllByRole("button").map((b) => b.textContent);
    expect(buttons.slice(0, 4)).toEqual(["Merge", "Send back", "Reject", "Run again"]);
    expect(buttons).not.toContain("Send to…");
  });

  it("answers in one click, and asks for words before a send-back", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ action: "merge", decision: REVIEW, by: "owner@example.com" }),
      expect.anything(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Send back" }));
    const send = within(screen.getByTestId("decision-words")).getByRole("button", { name: "Send back" });
    expect(send).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "Log the cause too");
    await userEvent.click(send);
    expect(mutate).toHaveBeenLastCalledWith(expect.objectContaining({ action: "send_back", text: "Log the cause too" }), expect.anything());
  });

  it("shows the quests on offer with what a yes starts, the held ones folded, and filters by kind", async () => {
    renderPage("/decisions?stage=offered");
    const cards = screen.getAllByTestId("decision-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText(/a job that removes it/)).toBeInTheDocument();
    expect(screen.getByTestId("decisions-held")).toHaveTextContent("Not asked in chat (1)");
    await userEvent.click(screen.getByRole("button", { name: "Crack" }));
    expect(screen.getAllByTestId("decision-card")).toHaveLength(1);
  });

  it("lets someone who may not answer read the cards without their buttons", () => {
    admin.current = false;
    renderPage();
    expect(within(screen.getByTestId("decision-card")).queryByRole("button", { name: "Merge" })).toBeNull();
  });
});

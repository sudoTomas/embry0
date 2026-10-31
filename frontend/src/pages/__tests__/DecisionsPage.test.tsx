import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Decision } from "@/api/decisions";
import type { LeadKind } from "@/api/leadKinds";
import { UiSettingsContext } from "@/hooks/useUiSettings";

const mockUseDecisions = vi.fn();
const mutate = vi.fn();
const send = vi.fn();
const giveBack = vi.fn();
const link = vi.fn();
const people = { current: [] as { email: string; name: string; github_login: string | null }[] };
vi.mock("@/hooks/useDecisions", () => ({
  useDecisions: (filters: unknown) => mockUseDecisions(filters),
  useAnswerDecision: () => ({ mutate, isPending: false }),
  usePeople: () => ({ data: people.current }),
  useSendDecision: () => ({ mutate: send, isPending: false }),
  useReturnHandoff: () => ({ mutate: giveBack, isPending: false }),
  useSetMyGithubLogin: () => ({ mutate: link, isPending: false }),
}));
const admin = { current: true };
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => admin.current,
  useAuthStore: (select: (s: { user: { email: string } }) => unknown) => select({ user: { email: "owner@example.com" } }),
}));
vi.mock("@/hooks/useRunWith", () => ({ useRunWithKeys: () => ({ data: [] }) }));
/** GET /lead-kinds as read (hooks/useLeadKinds): empty until a test loads the backend's catalogue. */
const catalogue = { current: new Map<string, LeadKind>() };
vi.mock("@/hooks/useLeadKinds", () => ({ useLeadKinds: () => catalogue.current }));
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
  send.mockReset();
  admin.current = true;
  catalogue.current = new Map();
  people.current = [
    { email: "owner@example.com", name: "Owner", github_login: "owner" },
    { email: "bo@example.com", name: "Bo", github_login: "bo" },
  ];
  mockUseDecisions.mockImplementation((filters: { for?: string }) =>
    filters.for === "me" ? { data: [] } : { data: [CRACK, REVIEW, WEED] },
  );
});

describe("DecisionsPage (decisions, spec 2026-10-27)", () => {
  it("opens on the quests to review, each card reading its verdict, facts and why before anything else", () => {
    renderPage("/decisions?stage=done");
    const cards = screen.getAllByTestId("decision-card");
    expect(cards).toHaveLength(1);
    const card = within(cards[0]);
    expect(card.getByText("Ready to merge")).toBeInTheDocument();
    expect(card.getByText("#44 · +212 −38 · 6 files")).toBeInTheDocument();
    expect(card.getByText("Users got 502s for two minutes.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Quests to review/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /Quests on offer/ })).toHaveTextContent("2");
  });

  it("offers the likely answer first, then sending it to someone better placed", () => {
    renderPage("/decisions?stage=done");
    const buttons = within(screen.getByTestId("decision-card")).getAllByRole("button").map((b) => b.textContent);
    expect(buttons.slice(0, 5)).toEqual(["Merge", "Send back", "Reject", "Run again", "Send to…"]);
  });

  it("sends a decision to a teammate with a note, never to yourself", async () => {
    renderPage("/decisions?stage=done");
    await userEvent.click(screen.getByRole("button", { name: "Send to…" }));
    const form = within(screen.getByTestId("decision-send-to"));
    expect(form.getAllByRole("option").map((o) => o.textContent)).toEqual(["Who should decide this?", "Bo (@bo)"]);
    expect(form.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.selectOptions(form.getByRole("combobox"), "bo@example.com");
    await userEvent.type(form.getByRole("textbox"), "Your area");
    await userEvent.click(form.getByRole("button", { name: "Send" }));
    expect(send).toHaveBeenCalledWith({ id: "done:job-1", to: "bo@example.com", note: "Your area" }, expect.anything());
  });

  it("opens on what waits for you; a quest sent to you is yours to answer, or to give back", async () => {
    admin.current = false;
    const sent = {
      ...REVIEW,
      assigned_to: { handoff_id: "ho-1", email: "owner@example.com", name: "Owner", github_login: "owner", from: "bo@example.com", note: "Yours", at: "2026-10-27T12:00:00+00:00" },
    };
    mockUseDecisions.mockImplementation((filters: { for?: string }) =>
      filters.for === "me" ? { data: [sent] } : { data: [CRACK, REVIEW, WEED] },
    );
    renderPage();
    expect(screen.getByRole("tab", { name: /Waiting for me/ })).toHaveAttribute("aria-selected", "true");
    const card = within(screen.getByTestId("decision-card"));
    expect(card.getByTestId("decision-assigned")).toHaveTextContent("Sent to you by bo@example.com: “Yours”");
    expect(card.getByRole("button", { name: "Merge" })).toBeInTheDocument();
    await userEvent.click(card.getByRole("button", { name: "Not mine" }));
    expect(giveBack).toHaveBeenCalledWith({ handoffId: "ho-1", note: "" }, expect.anything());
  });

  it("asks once for your GitHub login, so the quests your login owns wait for you", async () => {
    people.current = [{ email: "owner@example.com", name: "Owner", github_login: null }];
    renderPage();
    await userEvent.type(screen.getByRole("textbox", { name: "Your GitHub login" }), "octo");
    await userEvent.click(screen.getByRole("button", { name: "Link" }));
    expect(link).toHaveBeenCalledWith("octo", expect.anything());
  });

  it("answers in one click, and asks for words before a send-back", async () => {
    renderPage("/decisions?stage=done");
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

  it.each([true, false])(
    "names each kind in the filter as the catalogue says them, the game on or off (game: %s, #166)",
    async (game) => {
      catalogue.current = new Map([
        ["crack", { id: "crack", label: "Crack", plural: "Cracks", about: "a crack", found_by: "the scout" }],
        ["weed", { id: "weed", label: "Weed", plural: "Weeds", about: "dead code", found_by: "the scout" }],
      ]);
      render(
        <UiSettingsContext.Provider value={{ game, home: "guild" }}>
          <MemoryRouter initialEntries={["/decisions?stage=offered"]}>
            <DecisionsPage />
          </MemoryRouter>
        </UiSettingsContext.Provider>,
      );
      const filter = screen.getByLabelText("Kind");
      expect(
        within(filter).getAllByRole("button").map((b) => b.textContent),
        "the kinds filter says the catalogue's plural, as the quest board's does",
      ).toEqual(["All", "Cracks", "Weeds"]);
      await userEvent.click(within(filter).getByRole("button", { name: "Cracks" }));
      expect(screen.getAllByTestId("decision-card")).toHaveLength(1);
    },
  );

  it("lets someone who may not answer read the cards without their buttons", () => {
    admin.current = false;
    renderPage("/decisions?stage=done");
    expect(within(screen.getByTestId("decision-card")).queryByRole("button", { name: "Merge" })).toBeNull();
  });

  it("keeps the game's words while the game layer is on", () => {
    renderPage("/decisions?stage=done");
    expect(screen.getByText(/^Every quest waiting on someone/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Quests to review/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Quests on offer/ })).toBeInTheDocument();
  });

  it.each(["/decisions", "/decisions?stage=done", "/decisions?stage=offered"])(
    "says no quest at %s while the game layer is off (#177)",
    (path) => {
      // What the backend answers with the game off: a finished job is a "Job done".
      const plainReview = { ...REVIEW, kind_label: "Job done" };
      mockUseDecisions.mockImplementation((filters: { for?: string }) =>
        filters.for === "me" ? { data: [] } : { data: [CRACK, plainReview, WEED] },
      );
      render(
        <UiSettingsContext.Provider value={{ game: false, home: "guild" }}>
          <MemoryRouter initialEntries={[path]}>
            <DecisionsPage />
          </MemoryRouter>
        </UiSettingsContext.Provider>,
      );
      const words = screen.getByTestId("decisions-page").textContent ?? "";
      expect(words, `no game word on the page while the game is off: ${words}`).not.toMatch(/\bquests?\b/i);
      const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
      expect(tabs, "finished work is jobs to review, and what embry0 found is work on offer").toEqual([
        "Waiting for me0",
        "Jobs to review1",
        "Work on offer2",
      ]);
    },
  );
});

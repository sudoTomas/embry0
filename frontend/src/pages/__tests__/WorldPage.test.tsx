import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QuestScroll, World } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";

const state: { world: World | undefined; quest: QuestScroll | undefined } = {
  world: undefined,
  quest: undefined,
};
const send = vi.fn();
const rate = vi.fn();
const sendBack = vi.fn();
const reject = vi.fn();
const merge = vi.fn();
const again = vi.fn();
const dismiss = vi.fn();
const mutation = (fn: ReturnType<typeof vi.fn>) => ({
  mutate: fn,
  isPending: false,
});

vi.mock("@/hooks/useWorld", () => ({
  useWorld: () => ({ data: state.world, isError: false, refetch: vi.fn() }),
  useCharacter: () => ({ data: { quest_log: [] }, isLoading: false }),
  useQuest: () => ({ data: state.quest, isError: false }),
  useSendParty: () => mutation(send),
  useWriteQuest: () => mutation(vi.fn()),
  useRateQuest: () => mutation(rate),
  useSendBack: () => mutation(sendBack),
  useRejectQuest: () => mutation(reject),
  useApproveMerge: () => mutation(merge),
  useRunAgain: () => mutation(again),
  useDismissQuest: () => mutation(dismiss),
  useRecall: () => mutation(vi.fn()),
}));
vi.mock("@/hooks/useAgentIdentity", () => ({
  useIdentityLookup: () => (type: string) => identityOf(type),
  useAgentIdentity: (type: string) => identityOf(type),
}));
// Just enough art for the hall and the map to draw: a hall and a grass tileset, a few objects, one creature.
const ART = {
  version: 1,
  tileSize: 32,
  tilesets: {
    hall: {
      image: "tiles/hall.png",
      layout: "corner-4x4",
      terrains: { "0": "floor", "1": "wall" },
    },
    path: {
      image: "tiles/path.png",
      layout: "corner-4x4",
      terrains: { "0": "dirt", "1": "grass" },
    },
  },
  atlases: {
    objects: { image: "atlas/objects.png", width: 512, height: 256 },
    creatures: { image: "atlas/creatures.png", width: 512, height: 256 },
  },
  objects: Object.fromEntries(
    [
      "anvil",
      "desk",
      "lectern",
      "quest_board",
      "chest_closed",
      "chest_open",
      "chest_gold",
      "torch",
      "bookshelf",
      "guild_hall",
    ].map((name) => [
      name,
      {
        atlas: "objects",
        rect: [0, 0, 32, 32],
        anchor: { x: 16, y: 30 },
        bounds: [0, 0, 32, 32],
        category: "station",
      },
    ]),
  ),
  creatures: {
    case_bug: {
      atlas: "creatures",
      frames: [
        [0, 0, 32, 32],
        [32, 0, 32, 32],
      ],
      fps: 5,
      loop: true,
      size: 32,
      anchor: { x: 16, y: 30 },
      bounds: [0, 0, 32, 32],
      leadKinds: ["case"],
      interaction: "attack",
    },
  },
  vfx: {},
};
vi.mock("@/components/world/art/manifest", async (original) => ({
  ...(await original<object>()),
  useArt: () => ART,
}));
// An agent's panel watches a running job live; here the stream stays quiet.
vi.mock("@/hooks/useJobEventStream", () => ({
  useJobEventStream: () => ({ isConnected: false, isComplete: false }),
}));
vi.mock("@/api/inputs", () => ({
  fetchJobInputs: async () => [],
  answerInput: vi.fn(),
}));
vi.mock("@/hooks/useRunWith", () => ({ useRunWithKeys: () => ({ data: [] }) }));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({
    models: ["claude-sonnet-5"],
    providers: [],
    isLoading: false,
  }),
}));
vi.mock("@/stores/authStore", () => ({
  useIsAdmin: () => true,
  useAuthStore: (select: (s: { user: { email: string } }) => unknown) =>
    select({ user: { email: "player@example.com" } }),
}));

import { WorldPage } from "../WorldPage";

const character = (agent_type: string, over: Record<string, unknown> = {}) => ({
  agent_type,
  level: 40,
  total_level: 600,
  xp: 1000,
  tier: "steel",
  best_skill: "coding",
  capes: [],
  skills: [{ stat: "coding", level: 40, xp: 1000 }],
  state: "idle",
  job_id: null,
  quests_out: 0,
  stats: {
    quests: 4,
    succeeded: 3,
    failed: 1,
    merged: 2,
    success_rate: 0.75,
    avg_cost_usd: 1.2,
    avg_seconds: 600,
    last_quest_at: null,
    ratings: { good: 1, needs_work: 0, bad: 0 },
  },
  idle_too_long: false,
  quest_template: null,
  can_quest: true,
  ...over,
});

const WORLD = {
  generated_at: "2026-10-25T12:00:00Z",
  account: { xp: 1, total_level: 1200, characters: 2, quest_points: 0 },
  skills: [{ id: "coding", name: "Coding" }],
  characters: [
    character("developer", { state: "working", job_id: "job-live" }),
    character("why"),
  ],
  parties: [
    {
      job_id: "job-live",
      repo: "acme/web",
      area: "Billing",
      title: "Fix the sender",
      status: "running",
      state: "working",
      leader: "developer",
      party: ["developer"],
      working: "developer",
      lead_id: "case:3",
      template: null,
      since: null,
      finished_at: null,
      pr_url: null,
      delivery_state: null,
      stuck: true,
      failure: null,
    },
  ],
  quests: [
    {
      id: "case:9",
      kind: "case",
      repo: "acme/web",
      area: "Billing",
      owners: [],
      title: "Null in the invoice sender",
      summary: "The watcher keeps seeing this.",
      details: "",
      action: "Run the fix pipeline on the draft ticket",
      url: null,
      created_at: "2026-10-25T10:00:00Z",
      level: 55,
      verb: "Attack",
      creature: "case_bug",
      leader: "developer",
    },
  ],
  inbox: [
    {
      job_id: "job-1",
      repo: "acme/web",
      area: null,
      title: "Add the export",
      outcome: "pr_open",
      leader: "developer",
      pr_url: "https://github.com/acme/web/pull/7",
      finished_at: "2026-10-25T11:00:00Z",
      cost_usd: 2.5,
      failure: null,
    },
  ],
  loot: [],
  recent: [],
  regions: [
    {
      repo: "acme/web",
      jobs: 3,
      last_job_at: null,
      owned: true,
      watched: true,
      areas: [],
    },
  ],
  frontier: [],
} as unknown as World;

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
  pr_url: "https://github.com/acme/web/pull/7",
  pr: {
    number: 7,
    state: "open",
    files: 3,
    additions: 40,
    deletions: 2,
    commits: 1,
  },
  merge_approval: {
    id: "ma-1",
    status: "pending",
    pr_url: "https://github.com/acme/web/pull/7",
  },
  verification: null,
  party: [
    { agent_type: "developer", runs: 1, cost_usd: 2, seconds: 60, failed: 0 },
  ],
  xp: [{ agent_type: "developer", stat: "coding", xp: 1200 }],
  cost_usd: 2.5,
  seconds: 3600,
  started_at: null,
  finished_at: null,
  evaluation: null,
} as unknown as QuestScroll;

function page(path = "/") {
  return (
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <WorldPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderAt(path = "/") {
  return render(page(path));
}

beforeEach(() => {
  for (const fn of [send, rate, sendBack, reject, merge, again, dismiss]) fn.mockReset();
  state.world = WORLD;
  state.quest = SCROLL;
});

describe("WorldPage (the guild, spec 2026-10-25)", () => {
  it("shows who is out and where, the quests back to judge, and the board", () => {
    renderAt();
    const hall = screen.getByTestId("guild-hall");
    const cards = within(hall).getAllByTestId("world-character");
    expect(cards[0]).toHaveAttribute("data-state", "working");
    expect(cards[0]).toHaveTextContent("WorkingFix the sender · web · Billing");
    expect(within(cards[0]).getByTestId("stuck")).toBeInTheDocument();
    expect(screen.getByTestId("quest-inbox")).toHaveAttribute(
      "data-count",
      "1",
    );
    expect(
      within(screen.getByTestId("quest-board")).getByTestId("quest"),
    ).toHaveTextContent("Lvl 55");
  });

  it("starts a lead as the signed-in operator, with the model chosen", async () => {
    renderAt();
    await userEvent.click(
      within(screen.getByTestId("quest-board")).getByTestId("quest"),
    );
    const dialog = screen.getByTestId("send-party");
    await userEvent.click(
      within(dialog).getByRole("button", { name: /Run with/ }),
    );
    await userEvent.selectOptions(
      within(dialog).getByLabelText("Model"),
      "claude-sonnet-5",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Start" }),
    );
    expect(send.mock.calls[0][0]).toEqual({
      leadId: "case:9",
      by: "player@example.com",
      runWith: { model: "claude-sonnet-5" },
    });
  });

  it("opens a finished job's report from the inbox and reviews it from the bar at its foot: merge, send back, reject", async () => {
    renderAt();
    await userEvent.click(screen.getByTestId("inbox-quest"));
    expect(
      screen.getByRole("dialog", { name: "Add the export" }),
    ).toBeInTheDocument();
    const scroll = screen.getByTestId("quest-scroll");
    expect(within(scroll).getByTestId("quest-meta")).toHaveTextContent(
      "PR open",
    );
    expect(within(scroll).getByTestId("quest-loot")).toHaveTextContent(
      "Pull request #7 · open · 3 files · +40 −2",
    );
    expect(within(scroll).getByTestId("quest-xp")).toHaveTextContent(
      "Coding +1,200",
    );
    const bar = within(scroll).getByTestId("quest-judgement");
    expect(within(bar).getByTestId("review-position")).toHaveTextContent(
      "1 of 1",
    );
    expect(
      within(bar)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([
      "Merge",
      "Send back",
      "Reject",
      "Run again",
      "Run with: each agent's model, its usual key",
    ]);

    await userEvent.click(within(bar).getByRole("button", { name: "Merge" }));
    expect(merge.mock.calls[0][0]).toBe("ma-1");

    await userEvent.click(
      within(bar).getByRole("button", { name: "Send back" }),
    );
    expect(
      within(bar).getByRole("button", { name: "Send it back" }),
    ).toBeDisabled();
    await userEvent.type(
      within(bar).getByLabelText("A note on the work"),
      "Handle the empty list.",
    );
    await userEvent.click(
      within(bar).getByRole("button", { name: "Send it back" }),
    );
    expect(sendBack.mock.calls[0][0]).toEqual({
      jobId: "job-1",
      notes: "Handle the empty list.",
      runWith: {},
    });

    await userEvent.click(within(bar).getByRole("button", { name: "Reject" }));
    await userEvent.click(
      within(bar).getByRole("button", { name: "Reject the work" }),
    );
    expect(reject.mock.calls[0][0]).toEqual({
      jobId: "job-1",
      note: "Handle the empty list.",
      closePr: true,
    });
  });

  it("answers a failed quest right on its card: run it again, or dismiss it", async () => {
    state.world = {
      ...WORLD,
      inbox: [
        {
          ...WORLD.inbox[0],
          outcome: "failed",
          pr_url: null,
          failure: "the agent hit its turn cap",
        },
      ],
    };
    renderAt();
    const card = screen.getByTestId("inbox-quest").closest("li") as HTMLElement;
    await userEvent.click(
      within(card).getByRole("button", { name: "Run again" }),
    );
    expect(again.mock.calls.at(-1)?.[0]).toEqual({ jobId: "job-1" });
    await userEvent.click(
      within(card).getByRole("button", { name: "Dismiss" }),
    );
    expect(dismiss.mock.calls.at(-1)?.[0]).toBe("job-1");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("draws the hall's inside: each character at its station, the one out gone from it, the chest by the loot", async () => {
    state.world = {
      ...WORLD,
      loot: [
        {
          job_id: "job-1",
          repo: "acme/web",
          area: null,
          title: "Add the export",
          pr_url: "https://github.com/acme/web/pull/7",
          merged: true,
          at: null,
        },
      ],
    };
    renderAt();
    const scene = screen.getByTestId("hall-scene");
    const [developer, why] = within(scene).getAllByTestId("hall-character");
    expect(developer).toHaveAttribute("data-home", "false");
    expect(developer).toHaveAttribute("title", "Developer (out)");
    expect(why).toHaveAttribute("data-home", "true");

    await userEvent.click(why);
    expect(screen.getByTestId("character-panel")).toBeInTheDocument();

    const chest = within(scene).getByTestId("loot-chest");
    expect(chest).toHaveAttribute("data-chest", "chest_gold");
    await userEvent.click(chest);
    expect(screen.getByTestId("quest-scroll")).toBeInTheDocument();
  });

  it("puts each quest's creature on the map in its region, and a click on it sends a party", async () => {
    renderAt("/?view=map");
    const map = screen.getByTestId("world-map");
    expect(within(map).getByTestId("map-party")).toHaveAttribute(
      "data-state",
      "working",
    );
    await userEvent.click(within(map).getByTestId("map-lead"));
    expect(screen.getByTestId("send-party")).toBeInTheDocument();
  });

  it("says why a failed job failed", () => {
    state.quest = {
      ...SCROLL,
      outcome: "failed",
      status: "failed",
      failure: "it ran out of turns",
      pr_url: null,
      pr: null,
      merge_approval: null,
    } as QuestScroll;
    renderAt("/?quest=job-1");
    const scroll = screen.getByTestId("quest-scroll");
    expect(within(scroll).getByTestId("quest-meta")).toHaveTextContent(
      "Failed",
    );
    expect(within(scroll).getByTestId("quest-failure")).toHaveTextContent(
      "What went wrong: it ran out of turns.",
    );
    expect(
      within(scroll).queryByRole("button", { name: "Send back with notes" }),
    ).toBeNull();
  });
});

describe("closing what opens over the guild", () => {
  const openers: [string, () => HTMLElement][] = [
    ["a job's report", () => screen.getByTestId("inbox-quest")],
    [
      "an agent's panel",
      () =>
        within(screen.getByTestId("guild-hall")).getAllByTestId(
          "world-character",
        )[0],
    ],
    [
      "a lead",
      () => within(screen.getByTestId("quest-board")).getByTestId("quest"),
    ],
    ["a new task", () => screen.getByRole("button", { name: "New task" })],
  ];
  const closers: [string, () => Promise<unknown>][] = [
    [
      "its Close button",
      () =>
        userEvent.click(
          within(screen.getByRole("dialog")).getByRole("button", {
            name: "Close",
          }),
        ),
    ],
    ["Escape", () => userEvent.keyboard("{Escape}")],
    [
      "a click outside it",
      () => userEvent.click(screen.getByRole("dialog").parentElement!),
    ],
  ];
  for (const [what, opener] of openers) {
    for (const [how, close] of closers) {
      it(`closes ${what} with ${how}, and gives focus back to what opened it`, async () => {
        renderAt();
        const button = opener();
        await userEvent.click(button);
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        await close();
        expect(screen.queryByRole("dialog")).toBeNull();
        expect(button).toHaveFocus();
      });
    }
  }
});

describe("the activity box", () => {
  const LOST = {
    ...WORLD.parties[0],
    state: "defeated",
    status: "failed",
    stuck: false,
    failure:
      "the LLM account it ran on refused it: weekly limit · resets Oct 27",
    error_code: "ERR_PROVIDER_QUOTA_EXHAUSTED",
    error_message:
      "triage output malformed; developer: weekly limit · resets Oct 27",
  };
  beforeEach(() => window.localStorage.clear());

  it("copies a failure as a debug block, one line or all of them", async () => {
    const user = userEvent.setup();
    const client = new QueryClient();
    const tree = () => (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/"]}>
          <WorldPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    const { rerender } = render(tree());
    state.world = { ...WORLD, parties: [LOST] } as unknown as World;
    rerender(tree());
    const line = screen.getByTestId("world-chat-line");
    expect(line).toHaveTextContent(
      "Oh dear, Developer was defeated on “Fix the sender”: the LLM account it ran on refused it: weekly limit · resets Oct 27",
    );
    await user.click(
      within(line).getByRole("button", {
        name: "Copy this line for debugging",
      }),
    );
    const copied = await navigator.clipboard.readText();
    expect(copied).toMatch(/^time: \d{4}-\d\d-\d\dT/);
    expect(copied).toContain(
      "quest: Fix the sender\nagent: developer\nrepo: acme/web\narea: Billing\njob: job-live",
    );
    expect(copied).toContain(
      "error_code: ERR_PROVIDER_QUOTA_EXHAUSTED\nerror: triage output malformed; developer: weekly limit · resets Oct 27",
    );
    expect(copied).toContain("url: /jobs/job-live");
    await user.click(
      within(screen.getByTestId("world-chat")).getByRole("button", {
        name: "Copy all",
      }),
    );
    expect(await navigator.clipboard.readText()).toBe(copied);
  });

  it("folds to a tab and closes; the header opens it again, and the browser remembers", async () => {
    const { unmount } = renderAt();
    await userEvent.click(
      screen.getByRole("button", { name: "Collapse activity" }),
    );
    expect(screen.queryByTestId("world-chat")).toBeNull();
    await userEvent.click(screen.getByTestId("world-chat-tab"));
    await userEvent.click(
      within(screen.getByTestId("world-chat")).getByRole("button", {
        name: "Close activity",
      }),
    );
    expect(screen.queryByTestId("world-chat")).toBeNull();
    expect(screen.queryByTestId("world-chat-tab")).toBeNull();
    unmount();

    renderAt();
    expect(screen.queryByTestId("world-chat")).toBeNull();
    await userEvent.click(screen.getByTestId("show-activity"));
    expect(screen.getByTestId("world-chat")).toBeInTheDocument();
    expect(window.localStorage.getItem("embry0.world.chat")).toBe("open");
  });
});

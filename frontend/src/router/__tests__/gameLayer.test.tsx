import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider, onlineManager } from "@tanstack/react-query";
import { RouterProvider } from "react-router";

/**
 * The dashboard's real routes and shell, with the settings document's `ui`
 * section (#177) answered at the HTTP boundary: what `/` opens, what the
 * sidebar offers, which game pages are there, and what the shell reads.
 * The pages themselves are stubs; each page's own tests cover it.
 */

const http = vi.hoisted(() => ({
  /** What GET /config/ui answers: a body, or a status it fails with. */
  ui: { game: true, home: "guild" } as unknown,
  uiStatus: 200,
  /** How GET /config/ui answers: at once; never (the client gives up after its timeout, as axios does); or as a browser with no network. */
  uiMode: "answer" as "answer" | "never" | "offline",
  /** The timeout each GET /config/ui was sent with, and the timers that end the ones that never answer. */
  uiTimeouts: [] as (number | undefined)[],
  timers: [] as ReturnType<typeof setTimeout>[],
  /** Every path the dashboard asked for. */
  asked: [] as string[],
}));

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(async (path: string, config?: { timeout?: number }) => {
      http.asked.push(path);
      if (path === "/config/ui") {
        http.uiTimeouts.push(config?.timeout);
        if (http.uiMode === "offline") throw Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });
        if (http.uiMode === "never") {
          return new Promise((_, reject) => {
            if (config?.timeout) {
              http.timers.push(
                setTimeout(() => reject(Object.assign(new Error("timeout"), { code: "ECONNABORTED" })), config.timeout),
              );
            }
          });
        }
        if (http.uiStatus !== 200) throw Object.assign(new Error("refused"), { response: { status: http.uiStatus } });
        return { data: http.ui };
      }
      // Nothing else these stubs read has an answer; the shell copes with none.
      throw Object.assign(new Error("not here"), { response: { status: 404 } });
    }),
  },
}));

vi.mock("@/pages/WorldPage", () => ({ WorldPage: () => <h1>guild page</h1> }));
vi.mock("@/pages/DashboardPage", () => ({ DashboardPage: () => <h1>overview page</h1> }));
vi.mock("@/pages/AchievementsPage", () => ({ AchievementsPage: () => <h1>achievements page</h1> }));
vi.mock("@/pages/NotFoundPage", () => ({ NotFoundPage: () => <h1>not found page</h1> }));
vi.mock("@/components/progression/Chatbox", () => ({ Chatbox: () => <div data-testid="chatbox" /> }));
vi.mock("@/components/layout/TopBar", () => ({ TopBar: () => <header data-testid="topbar" /> }));
vi.mock("@/components/layout/OrchestratorStatus", () => ({ OrchestratorStatus: () => null }));
vi.mock("@/components/dashboard/BriefBar", () => ({ BriefBar: () => <div data-testid="brief" /> }));
vi.mock("@/hooks/useAwaitingIndicator", () => ({ useAwaitingIndicator: () => 0 }));

import { router } from "@/router";

let client: QueryClient;

beforeEach(() => {
  http.ui = { game: true, home: "guild" };
  http.uiStatus = 200;
  http.uiMode = "answer";
  http.uiTimeouts = [];
  http.asked = [];
  // Each test is a browser's first visit unless it keeps an answer itself.
  window.localStorage.clear();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  client.clear();
  onlineManager.setOnline(true);
  // A read a test left unanswered gives up with the test.
  http.timers.forEach(clearTimeout);
  http.timers = [];
});

/** Draw the dashboard at `path` without waiting for anything. */
async function draw(path: string) {
  await act(async () => {
    await router.navigate(path);
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

async function open(path: string) {
  await act(async () => {
    await router.navigate(path);
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  // The sidebar is drawn once the shell has read the section (or given up on it).
  await screen.findByTestId("sidebar");
}

/** The sidebar's Overview section, as `label href` per entry. */
function overviewSection(): string[] {
  const section = within(screen.getByTestId("sidebar")).getByRole("region", { name: "Overview" });
  return within(section)
    .getAllByRole("link")
    .map((link) => `${link.textContent?.trim()} ${link.getAttribute("href")}`);
}

function sidebarLabels(): string {
  return within(screen.getByTestId("sidebar"))
    .getAllByRole("link")
    .map((link) => link.textContent?.trim())
    .join(", ");
}

describe("the game layer, as the settings document's ui section sets it (#177)", () => {
  it("opens on the guild with every game page when nothing is written", async () => {
    await open("/");
    expect(await screen.findByRole("heading", { name: "guild page" })).toBeInTheDocument();
    expect(overviewSection()).toEqual(["Guild /", "Overview /overview", "Achievements /achievements"]);
    expect(screen.getByTestId("chatbox"), "the chat box is mounted").toBeInTheDocument();
    await waitFor(() => expect(http.asked).toContain("/progression"));
  });

  it("keeps the game for an older backend that has no such section", async () => {
    http.uiStatus = 404;
    await open("/");
    expect(await screen.findByRole("heading", { name: "guild page" })).toBeInTheDocument();
    expect(overviewSection()).toEqual(["Guild /", "Overview /overview", "Achievements /achievements"]);
  });

  it("with the game off opens on the Overview, offers no game page and reads no progression", async () => {
    http.ui = { game: false, home: "guild" };
    await open("/");
    expect(
      await screen.findByRole("heading", { name: "overview page" }),
      "with the game off, / is the Overview",
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "guild page" })).toBeNull();
    expect(overviewSection(), "the Overview section holds the Overview alone, at /").toEqual(["Overview /"]);
    expect(sidebarLabels(), "no game entry anywhere in the sidebar").not.toMatch(/Guild|Achievements/);
    expect(screen.queryByTestId("chatbox"), "no chat box").toBeNull();
    expect(screen.getByTestId("brief"), "the home keeps its brief").toBeInTheDocument();
    expect(http.asked, "nothing reads progression while the game is off").not.toContain("/progression");
  });

  it.each(["/achievements", "/guild", "/overview"])("with the game off sends %s home, to the Overview", async (path) => {
    http.ui = { game: false, home: "guild" };
    await open(path);
    expect(
      await screen.findByRole("heading", { name: "overview page" }),
      `${path} lands on the Overview at /`,
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });

  it("with the Overview as home keeps the game, with the guild at its own path", async () => {
    http.ui = { game: true, home: "overview" };
    await open("/");
    expect(await screen.findByRole("heading", { name: "overview page" }), "/ opens the Overview").toBeInTheDocument();
    expect(overviewSection()).toEqual(["Guild /guild", "Overview /", "Achievements /achievements"]);
    expect(screen.getByTestId("chatbox"), "the game is on: the chat box stays").toBeInTheDocument();

    await act(async () => {
      await router.navigate("/guild?character=developer");
    });
    expect(await screen.findByRole("heading", { name: "guild page" }), "the guild is at /guild").toBeInTheDocument();
    expect(router.state.location.search, "and keeps what it opens in the address").toBe("?character=developer");

    await act(async () => {
      await router.navigate("/overview");
    });
    expect(await screen.findByRole("heading", { name: "overview page" })).toBeInTheDocument();
    expect(router.state.location.pathname, "the home has one address").toBe("/");
  });

  it("applies a change at once when the settings document is saved, with no reload", async () => {
    await open("/");
    expect(await screen.findByRole("heading", { name: "guild page" })).toBeInTheDocument();
    expect(overviewSection()).toContain("Guild /");

    http.ui = { game: false, home: "guild" };
    // What the Policy tab's save, undo and conflict refresh (useSettingsDocument's refreshAll).
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["settings-document"] });
    });
    expect(
      await screen.findByRole("heading", { name: "overview page" }),
      "the home is the Overview once the save is read",
    ).toBeInTheDocument();
    expect(overviewSection()).toEqual(["Overview /"]);
    expect(screen.queryByTestId("chatbox")).toBeNull();
  });
  it("with the Overview as home sends a link into the guild to its own path, its query kept", async () => {
    http.ui = { game: true, home: "overview" };
    await open("/?quest=job-1&view=map");
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", { name: "guild page" }),
        "a link written while the guild was at / still opens the guild",
      ).not.toBeNull(),
    );
    expect(router.state.location.pathname, "at the guild's own path").toBe("/guild");
    expect(router.state.location.search, "with what it opens kept").toBe("?quest=job-1&view=map");

    await act(async () => {
      await router.navigate("/?ref=brief");
    });
    expect(await screen.findByRole("heading", { name: "overview page" }), "a query the guild does not read stays home").toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });

  it("with the game off opens the Overview for a link into the guild", async () => {
    http.ui = { game: false, home: "overview" };
    await open("/?quest=job-1");
    expect(await screen.findByRole("heading", { name: "overview page" }), "the guild is not there to open").toBeInTheDocument();
    expect(router.state.location.pathname, "and nothing redirects").toBe("/");
  });

  it("on a first visit waits for the read, then draws with the defaults once a read that never answers times out", async () => {
    http.uiMode = "never";
    await draw("/");
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 200));
    });
    expect(screen.queryByTestId("sidebar"), "a first visit waits for the read, so the game never flashes").toBeNull();
    expect(screen.queryByRole("heading", { name: "guild page" })).toBeNull();
    await waitFor(
      () => expect(screen.queryByTestId("sidebar"), "a read that never answers gives up and the shell is drawn").not.toBeNull(),
      { timeout: 4_500 },
    );
    expect(http.uiTimeouts.length, "one read, not retried").toBe(1);
    expect(http.uiTimeouts[0], "sent with a short timeout of its own").toBeLessThanOrEqual(3_000);
    expect(await screen.findByRole("heading", { name: "guild page" }), "and the defaults are drawn").toBeInTheDocument();
  }, 10_000);

  it("draws the shell for a browser that says it is offline", async () => {
    onlineManager.setOnline(false);
    http.uiMode = "offline";
    await draw("/");
    await waitFor(
      () => expect(screen.queryByTestId("sidebar"), "an offline browser still reads (and fails) instead of waiting forever").not.toBeNull(),
      { timeout: 2_000 },
    );
    expect(overviewSection(), "with the defaults").toEqual(["Guild /", "Overview /overview", "Achievements /achievements"]);
  });

  it("draws at once from the answer this browser kept, and reads the section again behind it", async () => {
    window.localStorage.setItem("embry0.ui-settings", JSON.stringify({ game: false, home: "guild" }));
    http.uiMode = "never";
    await draw("/");
    expect(screen.queryByTestId("sidebar"), "a kept answer draws the shell with no wait").not.toBeNull();
    expect(overviewSection(), "as the kept answer says: the game off").toEqual(["Overview /"]);
    expect(await screen.findByRole("heading", { name: "overview page" })).toBeInTheDocument();
    expect(http.asked, "the section is read again behind it").toContain("/config/ui");
    expect(http.asked, "and nothing reads progression").not.toContain("/progression");
  });

  it("keeps each answer for the browser's next visit", async () => {
    http.ui = { game: false, home: "overview" };
    await open("/");
    await waitFor(() =>
      expect(JSON.parse(window.localStorage.getItem("embry0.ui-settings") ?? "null"), "the answer is kept").toEqual({
        game: false,
        home: "overview",
      }),
    );
  });
});

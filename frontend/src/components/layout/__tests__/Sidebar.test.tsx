import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { QueueResponse } from "@/lib/types";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/stores/layoutStore", () => ({
  useLayoutStore: (selector?: (s: { sidebarOpen: boolean }) => unknown) =>
    selector ? selector({ sidebarOpen: true }) : { sidebarOpen: true },
}));

// The sidebar polls /queue for the Console awaiting-input badge — mock the
// api boundary so tests control the count without hitting axios.
const mockFetchQueue = vi.fn<() => Promise<QueueResponse>>();
vi.mock("@/api/queue", () => ({
  fetchQueue: () => mockFetchQueue(),
}));

import { Sidebar } from "../Sidebar";

function renderSidebar(path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ status: "checking", authMode: null, user: null, keyTier: undefined });
  mockFetchQueue.mockResolvedValue({
    depth: 0,
    pending: 0,
    running: 0,
    awaiting_input: 0,
    paused: 0,
  });
  document.title = "embry0 — Software that is alive";
});

describe("Sidebar — unified IA (ticket 011)", () => {
  it("renders the six section labels in order: Overview, Work, Pipelines & QA, Operations, Infra, Settings", () => {
    renderSidebar();
    const labels = screen
      .getAllByRole("heading", { level: 3 })
      .map((h) => h.textContent?.trim());
    expect(labels).toEqual([
      "Overview",
      "Work",
      "Pipelines & QA",
      "Operations",
      "Infra",
      "Settings",
    ]);
  });

  it("shows a quiet uppercase label over each group but Overview, a group of one", () => {
    renderSidebar();
    const work = screen.getByRole("heading", { level: 3, name: "Work" });
    expect(work.className).not.toContain("sr-only");
    expect(work.className).toContain("uppercase");
    expect(work.className).toContain("text-fg-muted");
    expect(screen.getByRole("heading", { level: 3, name: "Overview" }).className).toContain("sr-only");
  });

  it("Operations section contains Watcher, Logs in that order (Deploys removed with the RAV-658 deployer)", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Operations" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Watcher",
      "Logs",
    ]);
  });

  it("Work section contains Console, Issues, Jobs, Merge approvals in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Work" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Console",
      "Issues",
      "Jobs",
      "Merge approvals",
    ]);
  });

  it("Infra section contains Sandboxes, Hosts, Agents, Environments, Credentials in that order", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Sandboxes",
      "Hosts",
      "Agents",
      "Environments",
      "Credentials",
    ]);
  });

  it("Infra section adds Skills after Agents for an admin session, Hosts after Sandboxes", () => {
    useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "a@example.com", name: "A", tier: "admin" } });
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Sandboxes",
      "Hosts",
      "Agents",
      "Skills",
      "Environments",
      "Credentials",
    ]);
    expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("href", "/skills");
  });

  it("Infra section hides Skills from the operator tier", () => {
    useAuthStore.setState({ status: "authenticated", authMode: "session", user: { email: "o@example.com", name: "O", tier: "operator" } });
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    expect(within(section).queryByRole("link", { name: "Skills" })).toBeNull();
  });

  it("Infra section hides Skills from a key session whose tier is operator (#101)", () => {
    useAuthStore.setState({ status: "authenticated", authMode: "key", user: null, keyTier: "operator" });
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Infra" })
      .parentElement as HTMLElement;
    expect(
      within(section).queryByRole("link", { name: "Skills" }),
      "an operate-scope key is not admin",
    ).toBeNull();
  });

  it("Infra section shows Skills to a key session whose tier is admin", () => {
    useAuthStore.setState({ status: "authenticated", authMode: "key", user: null, keyTier: "admin" });
    renderSidebar();
    expect(screen.getByRole("link", { name: "Skills" })).toHaveAttribute("href", "/skills");
  });

  it("Pipelines & QA section contains Pipelines, Repos, Organizations, QA (central registry, ADR 0010)", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Pipelines & QA" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual([
      "Pipelines",
      "Repos",
      "Organizations",
      "QA",
    ]);
  });

  it("Settings section contains Settings, LLM providers", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Settings" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links.map((a) => a.textContent?.trim())).toEqual(["Settings", "LLM providers"]);
  });

  it("Overview section contains a single Overview link to /", () => {
    renderSidebar();
    const section = screen.getByRole("heading", { level: 3, name: "Overview" })
      .parentElement as HTMLElement;
    const links = within(section).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/");
    expect(links[0].textContent?.trim()).toBe("Overview");
  });

  it("every nav item points to its expected route (blocker-ticket coverage check)", () => {
    renderSidebar();
    const expectedHrefs: Record<string, string> = {
      Overview: "/",
      Console: "/console",
      Issues: "/issues",
      Jobs: "/jobs",
      Pipelines: "/pipelines",
      Repos: "/repos",
      Organizations: "/orgs",
      QA: "/qa/repos",
      "LLM providers": "/providers",
      Watcher: "/watcher",
      Logs: "/logs",
      Sandboxes: "/sandboxes",
      Agents: "/agents",
      Environments: "/environments",
      Credentials: "/credentials",
      Settings: "/settings",
    };
    for (const [label, href] of Object.entries(expectedHrefs)) {
      const link = screen.getByRole("link", { name: label });
      expect(link, `nav link "${label}"`).toHaveAttribute("href", href);
    }
  });
});

describe("Sidebar — the orchestrator's live state at its foot", () => {
  it("reads live once the queue poll answers", async () => {
    renderSidebar();
    const pill = screen.getByTestId("orchestrator-status");
    await waitFor(() => expect(pill).toHaveAttribute("data-state", "live"));
    expect(within(pill).getByRole("status")).toHaveAccessibleName("Orchestrator live");
  });

  it("reads unreachable when the queue poll fails", async () => {
    mockFetchQueue.mockRejectedValue(new Error("down"));
    renderSidebar();
    const pill = screen.getByTestId("orchestrator-status");
    await waitFor(() => expect(pill).toHaveAttribute("data-state", "down"));
    expect(within(pill).getByRole("status")).toHaveAccessibleName("Orchestrator unreachable");
  });
});

describe("Sidebar — Console awaiting-input prominence", () => {
  it("badges the Console entry with the awaiting_input + paused count", async () => {
    mockFetchQueue.mockResolvedValue({
      depth: 3,
      pending: 1,
      running: 0,
      awaiting_input: 1,
      paused: 1,
    });
    renderSidebar();
    const badge = await screen.findByTestId("nav-awaiting-badge");
    expect(badge).toHaveTextContent("2");
    expect(screen.getByRole("link", { name: /Console/ })).toContainElement(badge);
  });

  it("hides the badge at zero", async () => {
    renderSidebar();
    // Let the queue query settle before asserting absence.
    await waitFor(() => expect(mockFetchQueue).toHaveBeenCalled());
    expect(screen.queryByTestId("nav-awaiting-badge")).toBeNull();
  });

  it('mirrors the count into document.title as "(2⚠) …" and clears it at zero', async () => {
    mockFetchQueue.mockResolvedValue({
      depth: 2,
      pending: 0,
      running: 0,
      awaiting_input: 2,
      paused: 0,
    });
    const { unmount } = renderSidebar();
    await waitFor(() =>
      expect(document.title).toBe("(2⚠) embry0 — Software that is alive"),
    );
    // Unmount restores the unbadged title.
    unmount();
    expect(document.title).toBe("embry0 — Software that is alive");
  });
});

describe("Sidebar — the active item's marker travels with the route", () => {
  /** jsdom has no matchMedia, which reads as reduced motion; this says no
   * (and not narrow, so the desktop column stays). */
  function allowMotion() {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })),
    );
  }

  /** jsdom lays nothing out: give an element the box a browser would. */
  function placeBox(el: Element, top: number, height = 32) {
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      top,
      height,
      bottom: top + height,
      left: 0,
      right: 224,
      width: 224,
      x: 0,
      y: top,
      toJSON: () => ({}),
    } as DOMRect);
  }

  function markerAndNav() {
    const marker = screen.getByTestId("nav-active-marker");
    const nav = marker.parentElement;
    if (!nav) throw new Error("the marker sits in no nav");
    return { marker, nav };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("stands one quiet marker at the active item, inside the nav", () => {
    renderSidebar("/jobs");
    expect(screen.getAllByTestId("nav-active-marker")).toHaveLength(1);
    const { marker, nav } = markerAndNav();
    expect(nav.tagName).toBe("NAV");
    expect(marker.hidden).toBe(false);
    expect(marker).toHaveAttribute("aria-hidden", "true");
    expect(marker.className).toContain("bg-primary");
  });

  it("shows no marker where no item is active", () => {
    renderSidebar("/about/operations");
    expect(markerAndNav().marker.hidden).toBe(true);
  });

  it("glides the marker to the item the route lands on", () => {
    allowMotion();
    renderSidebar("/");
    const { marker, nav } = markerAndNav();
    expect(marker.hasAttribute("data-glide"), "the first placement jumps").toBe(false);
    placeBox(nav, 100);
    placeBox(screen.getByRole("link", { name: "Jobs" }), 300);
    fireEvent.click(screen.getByRole("link", { name: "Jobs" }));
    expect(marker.style.transform).toBe("translateY(200px) scaleY(1)");
    expect(marker.hasAttribute("data-glide"), "a move to another item glides").toBe(true);
  });

  it("jumps to the item under reduced motion", () => {
    renderSidebar("/");
    const { marker, nav } = markerAndNav();
    placeBox(nav, 100);
    placeBox(screen.getByRole("link", { name: "Jobs" }), 300);
    fireEvent.click(screen.getByRole("link", { name: "Jobs" }));
    expect(marker.style.transform).toBe("translateY(200px) scaleY(1)");
    expect(marker.hasAttribute("data-glide")).toBe(false);
  });

  it("re-places at once when a section's height moves the item, and not otherwise", () => {
    const reports: ResizeObserverCallback[] = [];
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(report: ResizeObserverCallback) {
          reports.push(report);
        }
        observe() {}
        disconnect() {}
      },
    );
    allowMotion();
    renderSidebar("/");
    const { marker, nav } = markerAndNav();
    const jobs = screen.getByRole("link", { name: "Jobs" });
    placeBox(nav, 100);
    placeBox(jobs, 300);
    fireEvent.click(jobs);
    const report = reports[reports.length - 1];
    const section = nav.querySelector("section") as Element;
    const sized = (height: number) =>
      [{ target: section, contentRect: { height } }] as unknown as ResizeObserverEntry[];

    // An observer's first report moves nothing: the glide under way stays.
    report(sized(40), {} as ResizeObserver);
    expect(marker.hasAttribute("data-glide")).toBe(true);
    expect(marker.style.transform).toBe("translateY(200px) scaleY(1)");

    // A section grew and the item moved: the marker follows at once.
    placeBox(jobs, 260);
    report(sized(0), {} as ResizeObserver);
    expect(marker.style.transform).toBe("translateY(160px) scaleY(1)");
    expect(marker.hasAttribute("data-glide")).toBe(false);
  });
});


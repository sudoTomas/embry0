import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { AgentNotification } from "@/api/agent";

const mockFetchNotifications = vi.fn();
const mockMarkAllRead = vi.fn();
vi.mock("@/api/agent", async () => {
  const actual = await vi.importActual<typeof import("@/api/agent")>("@/api/agent");
  return {
    ...actual,
    fetchNotifications: () => mockFetchNotifications(),
    markAllNotificationsRead: () => mockMarkAllRead(),
  };
});

import { NotificationsCenter } from "../NotificationsCenter";

function renderCenter() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <NotificationsCenter />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const NOTIF_WITH_HREF: AgentNotification = {
  id: "n-1",
  ts: new Date().toISOString(),
  title: "Job 42 finished",
  body: "All tasks green",
  level: "success",
  read: false,
  href: "/jobs/42",
};

const NOTIF_READ_NO_HREF: AgentNotification = {
  id: "n-2",
  ts: new Date().toISOString(),
  title: "Memory written",
  level: "info",
  read: true,
};

beforeEach(() => {
  mockFetchNotifications.mockReset();
  mockMarkAllRead.mockReset();
});

describe("NotificationsCenter", () => {
  it("renders a bell trigger that opens a dropdown", async () => {
    mockFetchNotifications.mockResolvedValue([]);
    renderCenter();

    const trigger = screen.getByLabelText(/notifications/i);
    expect(trigger).toBeInTheDocument();
    expect(screen.queryByTestId("notifications-dropdown")).toBeNull();

    fireEvent.click(trigger);
    expect(await screen.findByTestId("notifications-dropdown")).toBeInTheDocument();
  });

  it("shows the unread count on the bell when there are unread notifications", async () => {
    mockFetchNotifications.mockResolvedValue([
      NOTIF_WITH_HREF,
      NOTIF_READ_NO_HREF,
    ]);
    renderCenter();

    expect(await screen.findByTestId("notifications-unread-count")).toHaveTextContent(
      "1",
    );
  });

  it("hides the unread badge entirely when zero unread", async () => {
    mockFetchNotifications.mockResolvedValue([NOTIF_READ_NO_HREF]);
    renderCenter();
    // Wait for the query to settle by opening the dropdown.
    fireEvent.click(screen.getByLabelText(/notifications/i));
    await screen.findByTestId("notifications-dropdown");
    expect(screen.queryByTestId("notifications-unread-count")).toBeNull();
  });

  it("renders an empty state when there are no notifications", async () => {
    mockFetchNotifications.mockResolvedValue([]);
    renderCenter();

    fireEvent.click(screen.getByLabelText(/notifications/i));
    expect(await screen.findByTestId("notifications-empty")).toBeInTheDocument();
  });

  it("renders a row per notification with title and body", async () => {
    mockFetchNotifications.mockResolvedValue([NOTIF_WITH_HREF, NOTIF_READ_NO_HREF]);
    renderCenter();

    fireEvent.click(screen.getByLabelText(/notifications/i));
    expect(await screen.findByTestId("notification-row-n-1")).toHaveTextContent(
      "Job 42 finished",
    );
    expect(screen.getByTestId("notification-row-n-1")).toHaveTextContent("All tasks green");
    expect(screen.getByTestId("notification-row-n-2")).toHaveTextContent(
      "Memory written",
    );
  });

  it("renders rows with an href as anchors pointing at that target", async () => {
    mockFetchNotifications.mockResolvedValue([NOTIF_WITH_HREF, NOTIF_READ_NO_HREF]);
    renderCenter();

    fireEvent.click(screen.getByLabelText(/notifications/i));
    const link = await screen.findByTestId("notification-link-n-1");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/jobs/42");

    // The row without href does not render an anchor.
    expect(screen.queryByTestId("notification-link-n-2")).toBeNull();
  });

  it("mark-all-read button calls the mutation and invalidates the list", async () => {
    mockFetchNotifications.mockResolvedValue([NOTIF_WITH_HREF]);
    mockMarkAllRead.mockResolvedValue(undefined);
    renderCenter();

    fireEvent.click(screen.getByLabelText(/notifications/i));
    // Wait for notifications to load so the button is enabled.
    await screen.findByTestId("notification-row-n-1");
    fireEvent.click(screen.getByRole("button", { name: /mark all read/i }));

    await waitFor(() => expect(mockMarkAllRead).toHaveBeenCalledTimes(1));
    // After the mutation, the query re-runs.
    await waitFor(() =>
      expect(mockFetchNotifications.mock.calls.length).toBeGreaterThanOrEqual(2),
    );
  });

  it("mark-all-read button is disabled when zero unread", async () => {
    mockFetchNotifications.mockResolvedValue([NOTIF_READ_NO_HREF]);
    renderCenter();

    fireEvent.click(screen.getByLabelText(/notifications/i));
    // Wait for the data to settle so we observe the post-load disabled state.
    await screen.findByTestId("notification-row-n-2");
    expect(screen.getByRole("button", { name: /mark all read/i })).toBeDisabled();
  });
});

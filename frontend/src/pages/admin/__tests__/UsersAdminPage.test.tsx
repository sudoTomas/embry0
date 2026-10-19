import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchUsers = vi.fn();
const mockSetDisabled = vi.fn();
const mockForceLogout = vi.fn();
vi.mock("@/api/users", () => ({
  fetchUsers: (...a: unknown[]) => mockFetchUsers(...a),
  setUserDisabled: (...a: unknown[]) => mockSetDisabled(...a),
  forceLogoutUser: (...a: unknown[]) => mockForceLogout(...a),
}));

import { UsersAdminPage } from "../UsersAdminPage";

const USER = {
  id: "usr-1",
  auth0_sub: "waad|X|1",
  email: "t@example.com",
  name: "Alex",
  tier: "admin" as const,
  disabled: false,
  session_epoch: 1,
  login_count: 5,
  first_login_at: null,
  last_login_at: "2026-08-03T12:00:00Z",
  created_at: null,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UsersAdminPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchUsers.mockReset();
  mockSetDisabled.mockReset();
  mockForceLogout.mockReset();
});

describe("UsersAdminPage", () => {
  it("renders mirrored users with tier and status", async () => {
    mockFetchUsers.mockResolvedValue({ users: [USER], total: 1 });
    renderPage();
    expect(await screen.findByText("t@example.com")).toBeInTheDocument();
    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("shows the empty state before any SSO login", async () => {
    mockFetchUsers.mockResolvedValue({ users: [], total: 0 });
    renderPage();
    expect(await screen.findByText(/no users yet/i)).toBeInTheDocument();
  });

  it("disables a user after confirmation", async () => {
    mockFetchUsers.mockResolvedValue({ users: [USER], total: 1 });
    mockSetDisabled.mockResolvedValue({ ...USER, disabled: true });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByTitle(/disable access/i));
    expect(mockSetDisabled).toHaveBeenCalledWith("usr-1", true);
  });

  it("does nothing when confirmation is declined", async () => {
    mockFetchUsers.mockResolvedValue({ users: [USER], total: 1 });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByTitle(/sign this user out/i));
    expect(mockForceLogout).not.toHaveBeenCalled();
  });
});

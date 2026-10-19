import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { DOCKER_ACCESS_WARNING, RepoDockerAccessSection } from "../RepoDockerAccessSection";

const mockSetAccess = vi.fn();
vi.mock("@/api/repos", () => ({
  setRepoDockerAccess: (...args: unknown[]) => mockSetAccess(...args),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderSection(dockerAccess: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RepoDockerAccessSection repo="acme/widgets" dockerAccess={dockerAccess} />
    </QueryClientProvider>,
  );
}

const asTier = (tier: string) =>
  useAuthStore.setState({
    status: "authenticated",
    authMode: "session",
    user: { email: "u@example.com", name: "U", tier },
    ssoEnabled: true,
    apiKeyLogin: true,
  });

describe("RepoDockerAccessSection (#522)", () => {
  beforeEach(() => {
    mockSetAccess.mockReset();
    mockSetAccess.mockResolvedValue({ repo: "acme/widgets", docker_access: true });
  });
  afterEach(() => vi.restoreAllMocks());

  it("says what the grant hands over, granted or not", () => {
    asTier("operator");
    renderSection(false);
    expect(screen.getByText(DOCKER_ACCESS_WARNING)).toBeInTheDocument();
    expect(DOCKER_ACCESS_WARNING).toMatch(/shared Docker daemon, including the proxies holding your tokens/);
    expect(screen.getByText("No Docker access")).toBeInTheDocument();
  });

  it("gives an operator no control over the grant", () => {
    asTier("operator");
    renderSection(true);
    expect(screen.getByText("Docker access granted")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Only an admin can change this.")).toBeInTheDocument();
  });

  it("asks an admin to confirm, quoting the warning, before granting", async () => {
    asTier("admin");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderSection(false);
    await userEvent.click(screen.getByRole("button", { name: "Grant Docker access" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining(DOCKER_ACCESS_WARNING));
    await waitFor(() => expect(mockSetAccess).toHaveBeenCalledWith("acme/widgets", true));
  });

  it("grants nothing when the admin cancels", async () => {
    asTier("admin");
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderSection(false);
    await userEvent.click(screen.getByRole("button", { name: "Grant Docker access" }));
    expect(mockSetAccess).not.toHaveBeenCalled();
  });

  it("revokes without a confirmation", async () => {
    asTier("admin");
    const confirm = vi.spyOn(window, "confirm");
    renderSection(true);
    await userEvent.click(screen.getByRole("button", { name: "Revoke Docker access" }));
    expect(confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(mockSetAccess).toHaveBeenCalledWith("acme/widgets", false));
  });
});

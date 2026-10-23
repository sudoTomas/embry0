import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockBuild = vi.fn();
vi.mock("@/api/qaDashboard", () => ({ buildQaImage: (...a: unknown[]) => mockBuild(...a) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { toast } from "sonner";
import { BuildImageButton } from "../BuildImageButton";

function renderButton() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BuildImageButton repo="acme/widgets" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockBuild.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.info).mockReset();
});

describe("BuildImageButton", () => {
  it("asks for the branch and whether to force, then builds and says so", async () => {
    mockBuild.mockResolvedValue({ repo: "acme/widgets", branch: "release", status: "built" });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    const dialog = screen.getByRole("dialog", { name: "Build QA image" });
    expect(dialog).toHaveTextContent(/can take minutes/i);
    const branch = screen.getByRole("textbox", { name: "Branch" });
    expect(branch).toHaveValue("main");
    expect(branch).toHaveFocus();
    await userEvent.clear(branch);
    await userEvent.type(branch, "release");
    await userEvent.click(screen.getByRole("switch", { name: "Rebuild even if nothing changed" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    await waitFor(() => expect(mockBuild).toHaveBeenCalledWith("acme/widgets", "release", true));
    expect(toast.success).toHaveBeenCalledWith("QA image built for acme/widgets (release)");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says the image was already current when the build was skipped", async () => {
    mockBuild.mockResolvedValue({ repo: "acme/widgets", branch: "main", status: "skipped" });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    await waitFor(() => expect(toast.info).toHaveBeenCalledWith("QA image already current for acme/widgets (main)"));
  });

  it("keeps the dialog open and shows the server's reason when the build is refused", async () => {
    mockBuild.mockRejectedValue({ response: { status: 422, data: { detail: "ERR_REPO_UNREGISTERED: acme/widgets is not registered" } } });
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("acme/widgets is not registered");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("refuses an empty branch before asking the server", async () => {
    renderButton();
    await userEvent.click(screen.getByRole("button", { name: "Build QA image" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Branch" }));
    await userEvent.click(screen.getByRole("button", { name: "Build" }));
    expect(mockBuild).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Name the branch to build from.");
  });
});

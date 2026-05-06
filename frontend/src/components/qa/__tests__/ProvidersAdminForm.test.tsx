import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

const mockUpsert = vi.fn();
vi.mock("@/api/qaDashboard", () => ({
  upsertProviderOverride: (
    repo: string,
    body: { provider_type: string; config: Record<string, unknown> },
  ) => mockUpsert(repo, body),
}));

// sonner is used by the page wrapper but not the form. The form has no
// toast() calls of its own, so no mock needed here.

import { ProvidersAdminForm } from "../ProvidersAdminForm";
import type { WorkspaceProviderOverride } from "@/lib/types";

const ROW: WorkspaceProviderOverride = {
  repo: "org/r1",
  provider_type: "npm-workspaces-turbo",
  config: { affected_filter: "[HEAD^1]", apps_glob: "apps/*" },
  updated_at: "2026-05-06T12:00:00Z",
};

beforeEach(() => {
  mockUpsert.mockReset();
});

describe("ProvidersAdminForm", () => {
  it("renders form with initial values when editing", () => {
    const onSaved = vi.fn();
    const onCancel = vi.fn();

    render(
      <ProvidersAdminForm initial={ROW} onSaved={onSaved} onCancel={onCancel} />,
    );

    const repo = screen.getByLabelText("Repo") as HTMLInputElement;
    expect(repo.value).toBe("org/r1");
    expect(repo).toBeDisabled();

    const providerType = screen.getByLabelText(
      "Provider type",
    ) as HTMLInputElement;
    expect(providerType.value).toBe("npm-workspaces-turbo");

    const config = screen.getByLabelText(
      /Config \(JSON object\)/i,
    ) as HTMLTextAreaElement;
    // Should pretty-print the existing config.
    expect(config.value).toContain("affected_filter");
    expect(config.value).toContain("[HEAD^1]");
    expect(config.value).toContain("apps_glob");

    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
  });

  it("validates config is valid JSON object before submit", async () => {
    const onSaved = vi.fn();
    const onCancel = vi.fn();

    render(<ProvidersAdminForm onSaved={onSaved} onCancel={onCancel} />);

    fireEvent.change(screen.getByLabelText("Repo"), {
      target: { value: "org/new" },
    });
    fireEvent.change(screen.getByLabelText(/Config/i), {
      target: { value: "[1, 2, 3]" }, // valid JSON, but it's an array
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      await screen.findByTestId("providers-admin-form-error"),
    ).toHaveTextContent(/must be a JSON object/i);
    expect(mockUpsert).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();

    // Now bad JSON syntax: should also block.
    fireEvent.change(screen.getByLabelText(/Config/i), {
      target: { value: "{ not valid json }" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(
      await screen.findByTestId("providers-admin-form-error"),
    ).toHaveTextContent(/Invalid JSON/i);
    expect(mockUpsert).not.toHaveBeenCalled();
  });

  it("submits via upsertProviderOverride with the parsed config object", async () => {
    const onSaved = vi.fn();
    const onCancel = vi.fn();
    mockUpsert.mockResolvedValue({
      repo: "org/new",
      provider_type: "npm-workspaces-turbo",
      config: { affected_filter: "[HEAD^1]" },
      updated_at: "2026-05-06T12:00:00Z",
    });

    render(<ProvidersAdminForm onSaved={onSaved} onCancel={onCancel} />);

    fireEvent.change(screen.getByLabelText("Repo"), {
      target: { value: "org/new" },
    });
    fireEvent.change(screen.getByLabelText(/Config/i), {
      target: { value: '{"affected_filter": "[HEAD^1]"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(mockUpsert).toHaveBeenCalledTimes(1));
    expect(mockUpsert).toHaveBeenCalledWith("org/new", {
      provider_type: "npm-workspaces-turbo",
      config: { affected_filter: "[HEAD^1]" },
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(onSaved).toHaveBeenCalledWith(
      expect.objectContaining({ repo: "org/new" }),
    );
  });

  it("shows error when API rejects the upsert", async () => {
    const onSaved = vi.fn();
    const onCancel = vi.fn();
    mockUpsert.mockRejectedValue(new Error("server exploded"));

    render(<ProvidersAdminForm onSaved={onSaved} onCancel={onCancel} />);
    fireEvent.change(screen.getByLabelText("Repo"), {
      target: { value: "org/new" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(
      await screen.findByTestId("providers-admin-form-error"),
    ).toHaveTextContent(/server exploded/);
    expect(onSaved).not.toHaveBeenCalled();
  });
});

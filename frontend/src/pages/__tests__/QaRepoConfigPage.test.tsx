import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { AxiosError, AxiosHeaders } from "axios";

const mockUseQaConfig = vi.fn();
const mockSaveMutate = vi.fn();
const mockSaveReset = vi.fn();
const mockDeleteMutate = vi.fn();
const mockUseSaveQaConfig = vi.fn();
const mockUseDeleteQaConfig = vi.fn();
vi.mock("@/hooks/useQaConfig", () => ({
  useQaConfig: () => mockUseQaConfig(),
  useSaveQaConfig: () => mockUseSaveQaConfig(),
  useDeleteQaConfig: () => mockUseDeleteQaConfig(),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) },
}));

import { QaRepoConfigPage } from "../QaRepoConfigPage";

const YAML = "version: 2\nqa_required: never\n";

function make422(detail: string): AxiosError {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("Request failed with status code 422", "422", config, undefined, {
    data: { detail },
    status: 422,
    statusText: "",
    headers: {},
    config,
  } as never);
}

function renderPage(repo = "owner/name") {
  return render(
    <MemoryRouter initialEntries={[`/qa/repos/${encodeURIComponent(repo)}/config`]}>
      <Routes>
        <Route path="/qa/repos/:repo/config" element={<QaRepoConfigPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockUseQaConfig.mockReset();
  mockSaveMutate.mockReset();
  mockSaveReset.mockReset();
  mockDeleteMutate.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  mockUseSaveQaConfig.mockReturnValue({
    mutate: mockSaveMutate,
    reset: mockSaveReset,
    error: null,
    isPending: false,
  });
  mockUseDeleteQaConfig.mockReturnValue({
    mutate: mockDeleteMutate,
    isPending: false,
  });
});

describe("QaRepoConfigPage", () => {
  it("shows a loading skeleton while fetching", () => {
    mockUseQaConfig.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
    renderPage();
    expect(screen.queryByTestId("qa-config-textarea")).not.toBeInTheDocument();
  });

  it("renders the not-set-up empty state with an encoded form link on 404", () => {
    mockUseQaConfig.mockReturnValue({ data: null, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();

    expect(screen.getByText("Testing is not set up yet")).toBeInTheDocument();
    expect(screen.getByTestId("qa-config-bootstrap-cta")).toHaveAttribute(
      "href",
      "/repos/onboard?repo=owner%2Fname",
    );
  });

  it("seeds the editor from the template CTA", () => {
    mockUseQaConfig.mockReturnValue({ data: null, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();

    fireEvent.click(screen.getByTestId("qa-config-template-cta"));
    const textarea = screen.getByTestId("qa-config-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toContain("version: 2");
    expect(screen.getByTestId("qa-config-save")).toBeEnabled();
  });

  it("populates the editor from the stored YAML and disables Save until edited", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();

    const textarea = screen.getByTestId("qa-config-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe(YAML);
    expect(screen.getByTestId("qa-config-save")).toBeDisabled();

    fireEvent.change(textarea, { target: { value: YAML + "parallelism:\n  max_concurrent_apps: 4\n" } });
    expect(screen.getByTestId("qa-config-save")).toBeEnabled();
  });

  it("saves with the full repo path payload", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();

    const edited = YAML + "# touched\n";
    fireEvent.change(screen.getByTestId("qa-config-textarea"), { target: { value: edited } });
    fireEvent.click(screen.getByTestId("qa-config-save"));

    expect(mockSaveMutate).toHaveBeenCalledWith(
      { fullRepo: "owner/name", yamlText: edited },
      expect.anything(),
    );
  });

  it("renders the 422 detail verbatim in a <pre> without toasting", () => {
    const detail =
      "qa.yaml v2 validation failed: 1 validation error for QAYamlConfigV2\napps.web.frontend_url\n  Field required";
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    mockUseSaveQaConfig.mockReturnValue({
      mutate: mockSaveMutate,
      reset: mockSaveReset,
      error: make422(detail),
      isPending: false,
    });
    renderPage();

    expect(screen.getByTestId("qa-config-validation-error")).toHaveTextContent("Field required");
    expect(screen.getByTestId("qa-config-validation-error").textContent).toBe(detail);
    expect(toastError).not.toHaveBeenCalled();
  });

  it("resets the save error when the text changes again", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    mockUseSaveQaConfig.mockReturnValue({
      mutate: mockSaveMutate,
      reset: mockSaveReset,
      error: make422("nope"),
      isPending: false,
    });
    renderPage();

    fireEvent.change(screen.getByTestId("qa-config-textarea"), { target: { value: "version: 2\n" } });
    expect(mockSaveReset).toHaveBeenCalled();
  });

  it("reverts the draft to the server text", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();

    const textarea = screen.getByTestId("qa-config-textarea") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "garbage" } });
    fireEvent.click(screen.getByTestId("qa-config-revert"));
    expect(textarea.value).toBe(YAML);
    expect(screen.getByTestId("qa-config-save")).toBeDisabled();
  });

  it("deletes only after confirmation", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    fireEvent.click(screen.getByTestId("qa-config-delete"));
    expect(mockDeleteMutate).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByTestId("qa-config-delete"));
    expect(mockDeleteMutate).toHaveBeenCalledWith(
      { fullRepo: "owner/name" },
      expect.anything(),
    );
    confirmSpy.mockRestore();
  });

  it("links back to the repository page, not the QA runs", () => {
    mockUseQaConfig.mockReturnValue({ data: YAML, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();
    expect(screen.getByRole("link", { name: "← Repository" })).toHaveAttribute("href", "/repos/owner/name");
  });

  it("rejects a repo param that is not owner/name", () => {
    mockUseQaConfig.mockReturnValue({ data: undefined, isLoading: false, isError: false, refetch: vi.fn() });
    renderPage("just-a-name");
    expect(screen.getByText(/Invalid repo/)).toBeInTheDocument();
  });
});

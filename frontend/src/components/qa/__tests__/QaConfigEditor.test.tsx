// The test setup editor, as a person who is not technical meets it: plain
// words, no promise the code does not keep, no gold of its own (the repo
// page's gold belongs to the setup card), and a confirm before Remove.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const mockUseQaConfig = vi.fn();
const mockSaveMutate = vi.fn();
const mockDeleteMutate = vi.fn();
vi.mock("@/hooks/useQaConfig", () => ({
  useQaConfig: () => mockUseQaConfig(),
  useSaveQaConfig: () => ({ mutate: mockSaveMutate, reset: vi.fn(), error: null, isPending: false }),
  useDeleteQaConfig: () => ({ mutate: mockDeleteMutate, isPending: false }),
}));

const toastSuccess = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() },
}));

import { QaConfigEditor } from "../QaConfigEditor";

const YAML = "version: 2\nqa_required: never\n";
const GOLD = /(^|\s)bg-primary(\s|$)/;

function renderEditor(repo = "acme/widgets") {
  return render(
    <MemoryRouter>
      <QaConfigEditor repo={repo} />
    </MemoryRouter>,
  );
}

function stored(text: string | null) {
  mockUseQaConfig.mockReturnValue({ data: text, isLoading: false, isError: false, refetch: vi.fn() });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("QaConfigEditor copy", () => {
  it("says what a test setup is, in plain words", () => {
    stored(YAML);
    renderEditor();
    expect(
      screen.getByText(
        "Test setup. How embry0 starts your app on its own test computer and what it checks after each change. embry0 keeps it itself, so nothing is added to your repository.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/source of truth/)).not.toBeInTheDocument();
  });

  it("says testing is not set up, and promises no in-repo file", () => {
    stored(null);
    renderEditor();
    expect(screen.getByText("Testing is not set up yet")).toBeInTheDocument();
    expect(
      screen.getByText("embry0 can still write code, but it cannot check that your app still works."),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\.embry0\/qa\.yaml|deprecated/);
  });
});

describe("QaConfigEditor actions", () => {
  it("offers the form and the template as outline buttons, with no gold", () => {
    stored(null);
    renderEditor("acme/group/widgets");
    const form = screen.getByRole("link", { name: "Fill in a form" });
    expect(form).toHaveAttribute("href", "/repos/onboard?repo=acme%2Fgroup%2Fwidgets");
    expect(form.className).toContain("border-border-strong");
    expect(form.className).not.toMatch(GOLD);
    expect(screen.queryByText("Bootstrap this repo")).not.toBeInTheDocument();
    const template = screen.getByRole("button", { name: "Start from template" });
    expect(template.className).not.toMatch(GOLD);
  });

  it("starts the template on a work machine with a browser", () => {
    stored(null);
    renderEditor();
    fireEvent.click(screen.getByTestId("qa-config-template-cta"));
    const text = (screen.getByTestId("qa-config-textarea") as HTMLTextAreaElement).value;
    expect(text).toContain("defaults:\n  sandbox_profile: qa-external\n");
  });

  it("saves with the secondary button, never the gold", () => {
    stored(YAML);
    renderEditor();
    const save = screen.getByTestId("qa-config-save");
    expect(save).toHaveTextContent("Save");
    expect(save.className).toContain("bg-overlay");
    expect(save.className).not.toMatch(GOLD);
  });
});

describe("QaConfigEditor remove", () => {
  it("names the action Remove test setup and says what removing does", () => {
    stored(YAML);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Remove test setup" }));
    expect(confirmSpy).toHaveBeenCalledWith(
      "Remove the test setup for acme/widgets? Jobs that need to test your app will stop at that step until a new setup is saved. You can undo this from History.",
    );
    expect(mockDeleteMutate).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("removes after the confirm and says it can be undone", () => {
    stored(YAML);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockDeleteMutate.mockImplementation((_vars, opts: { onSuccess: () => void }) => opts.onSuccess());
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Remove test setup" }));
    expect(mockDeleteMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets" }, expect.anything());
    expect(toastSuccess).toHaveBeenCalledWith("Test setup removed. You can undo this from History.");
    confirmSpy.mockRestore();
  });
});

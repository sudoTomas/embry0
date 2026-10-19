// The test setup editor, as a person who is not technical meets it: plain
// words, no promise the code does not keep, no gold of its own (the repo
// page's gold belongs to the setup card), and a confirm before Remove.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";

const mockUseQaConfig = vi.fn();
const mockSaveMutate = vi.fn();
const mockDeleteMutate = vi.fn();
const mockCheckMutate = vi.fn();
let mockCheckPending = false;
vi.mock("@/hooks/useQaConfig", () => ({
  useQaConfig: () => mockUseQaConfig(),
  useSaveQaConfig: () => ({ mutate: mockSaveMutate, reset: vi.fn(), error: null, isPending: false }),
  useDeleteQaConfig: () => ({ mutate: mockDeleteMutate, isPending: false }),
  useCheckQaConfig: () => ({ mutate: mockCheckMutate, isPending: mockCheckPending }),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({
  toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) },
}));

import type { QaCheckResult } from "@/api/qaConfig";
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
  mockCheckPending = false;
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

const EDITED = "version: 2\nqa_required: auto\n";

const OK: QaCheckResult = {
  ok: true,
  problems: [],
  warnings: [],
  summary: {
    when: "auto",
    when_plain: "When a change touches the app",
    apps: [
      {
        name: "web",
        target: "managed",
        start: "npm run dev",
        address: "http://localhost:5173",
        address_is_local: true,
        criteria: ["home page loads"],
        guardrails: 0,
        machine: "qa-external",
        machine_has_browser: true,
      },
    ],
  },
};

const NO_BROWSER: QaCheckResult = {
  ok: false,
  problems: [
    {
      where: "app web",
      plain: "The work machine 'slim' has no browser, so every test run would stop.",
      fix: "Use 'qa-external'.",
    },
  ],
  warnings: [],
  summary: OK.summary,
};

const LIVE_ADDRESS =
  "web is tested at https://www.example.com, which is outside embry0's test computer. Tests there click through a real site.";

const WITH_WARNING: QaCheckResult = {
  ...OK,
  warnings: [{ code: "live_address", plain: LIVE_ADDRESS, confirm: true }],
};

type MutateOpts<T> = { onSuccess: (data: T) => void; onError: (error: unknown) => void };

function checkAnswers(result: QaCheckResult) {
  mockCheckMutate.mockImplementation((_vars, opts: MutateOpts<QaCheckResult>) => opts.onSuccess(result));
}

function edit(text = EDITED) {
  fireEvent.change(screen.getByTestId("qa-config-textarea"), { target: { value: text } });
}

describe("QaConfigEditor check", () => {
  it("offers Check my setup as an outline button beside Save", () => {
    stored(YAML);
    renderEditor();
    const check = screen.getByRole("button", { name: "Check my setup" });
    expect(check.className).toContain("border-border-strong");
    expect(check.className).not.toMatch(GOLD);
    expect(check.parentElement).toBe(screen.getByTestId("qa-config-save").parentElement);
  });

  it("checks the text on screen, shows the result under the editor and saves nothing", () => {
    stored(YAML);
    checkAnswers(OK);
    renderEditor();
    edit();
    fireEvent.click(screen.getByRole("button", { name: "Check my setup" }));
    expect(mockCheckMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets", yamlText: EDITED }, expect.anything());
    expect(screen.getByText("All good: every app has a work machine with a browser.")).toBeInTheDocument();
    expect(
      screen.getByText("web: starts with npm run dev, tested at http://localhost:5173 on embry0's test computer, 1 check."),
    ).toBeInTheDocument();
    expect(mockSaveMutate).not.toHaveBeenCalled();
  });

  it("can check the stored setup before anything is edited", () => {
    stored(YAML);
    checkAnswers(OK);
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Check my setup" }));
    expect(mockCheckMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets", yamlText: YAML }, expect.anything());
  });

  it("says why when the check itself could not run", () => {
    stored(YAML);
    mockCheckMutate.mockImplementation((_vars, opts: MutateOpts<QaCheckResult>) => opts.onError(new Error("boom")));
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Check my setup" }));
    expect(toastError).toHaveBeenCalledWith("Could not check the setup: boom");
    expect(screen.queryByTestId("qa-check-result")).not.toBeInTheDocument();
  });

  it("waits while the check runs: both buttons are disabled", () => {
    stored(YAML);
    mockCheckPending = true;
    renderEditor();
    edit();
    expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
    expect(screen.getByTestId("qa-config-save")).toBeDisabled();
  });

  it("drops a result once the text changes, because it no longer describes it", () => {
    stored(YAML);
    checkAnswers(OK);
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Check my setup" }));
    expect(screen.getByTestId("qa-check-result")).toBeInTheDocument();
    edit();
    expect(screen.queryByTestId("qa-check-result")).not.toBeInTheDocument();
  });
});

describe("QaConfigEditor save runs the check first", () => {
  it("does not save a setup every test run would stop on, and shows why", () => {
    stored(YAML);
    checkAnswers(NO_BROWSER);
    renderEditor();
    edit();
    fireEvent.click(screen.getByTestId("qa-config-save"));
    expect(mockCheckMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets", yamlText: EDITED }, expect.anything());
    expect(mockSaveMutate).not.toHaveBeenCalled();
    expect(
      screen.getByText("The work machine 'slim' has no browser, so every test run would stop. Use 'qa-external'."),
    ).toBeInTheDocument();
    expect(toastError).toHaveBeenCalledWith(
      "Not saved. Fix the problems shown below; every test run would stop on them.",
    );
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("saves a setup that only has warnings, then shows the warnings", () => {
    stored(YAML);
    checkAnswers(WITH_WARNING);
    let finishSave: (() => void) | undefined;
    mockSaveMutate.mockImplementation((_vars, opts: { onSuccess: () => void }) => {
      finishSave = opts.onSuccess;
    });
    renderEditor();
    edit();
    fireEvent.click(screen.getByTestId("qa-config-save"));
    expect(mockSaveMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets", yamlText: EDITED }, expect.anything());
    // Nothing is said about the warnings until the save has landed.
    expect(screen.queryByText(LIVE_ADDRESS)).not.toBeInTheDocument();
    act(() => finishSave!());
    expect(toastSuccess).toHaveBeenCalledWith("Test setup saved");
    expect(screen.getByTestId("qa-check-warnings")).toHaveTextContent(LIVE_ADDRESS);
    expect(toastError).not.toHaveBeenCalled();
  });

  it("saves a clean setup as before and adds nothing under the editor", () => {
    stored(YAML);
    checkAnswers(OK);
    mockSaveMutate.mockImplementation((_vars, opts: { onSuccess: () => void }) => opts.onSuccess());
    renderEditor();
    edit();
    fireEvent.click(screen.getByTestId("qa-config-save"));
    expect(mockSaveMutate).toHaveBeenCalledTimes(1);
    expect(toastSuccess).toHaveBeenCalledWith("Test setup saved");
    expect(screen.queryByTestId("qa-check-result")).not.toBeInTheDocument();
  });

  it("saves as before when the check itself could not run; the server still validates", () => {
    stored(YAML);
    mockCheckMutate.mockImplementation((_vars, opts: MutateOpts<QaCheckResult>) => opts.onError(new Error("boom")));
    renderEditor();
    edit();
    fireEvent.click(screen.getByTestId("qa-config-save"));
    expect(mockSaveMutate).toHaveBeenCalledWith({ fullRepo: "acme/widgets", yamlText: EDITED }, expect.anything());
    expect(toastError).not.toHaveBeenCalled();
  });
});

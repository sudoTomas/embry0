import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { useAuthStore } from "@/stores/authStore";

const mockDirectory = vi.fn();
const mockSuggestions = vi.fn();
const create = vi.fn();
const update = vi.fn();
const remove = vi.fn();
vi.mock("@/hooks/usePeopleDirectory", () => ({
  usePeopleDirectory: (includeDisabled: boolean) => mockDirectory(includeDisabled),
  usePeopleSuggestions: () => mockSuggestions(),
  useCreatePerson: () => ({ mutate: create, isPending: false }),
  useUpdatePerson: () => ({ mutate: update, isPending: false }),
  useRemovePerson: () => ({ mutate: remove, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { PeoplePage } from "../PeoplePage";

const ADA = {
  id: "per-ada",
  name: "Ada",
  email: "ada@example.test",
  user_id: "usr-ada",
  timezone: "Europe/London",
  hours: "09-17",
  weekdays: null,
  disabled: false,
  identities: [
    { kind: "github", value: "ada-l" },
    { kind: "telegram", value: "ada_tg" },
  ],
  updated_at: "2026-11-25T09:00:00+00:00",
};
const PAT = { ...ADA, id: "per-pat", name: "Pat", email: null, user_id: null, timezone: null, hours: null, identities: [] };
const SUGGESTED = {
  users: [{ user_id: "usr-bo", email: "bo@example.test", name: "Bo", github_login: "bo-l" }],
  owners: [{ forge: "gitlab", login: "cy", repos: ["acme/web", "acme/api"] }],
};

function ready<T>(data: T) {
  return { data, isPending: false, isError: false, refetch: vi.fn() };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <PeoplePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  [create, update, remove].forEach((m) => m.mockReset());
  mockDirectory.mockReset().mockReturnValue(ready([ADA, PAT]));
  mockSuggestions.mockReset().mockReturnValue(ready(SUGGESTED));
  useAuthStore.setState({ authMode: "key", user: null });
});

describe("PeoplePage", () => {
  it("lists each person with their identities, their hours and whether they sign in", () => {
    renderPage();
    const rows = screen.getAllByTestId("person-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("GitHub ada-l")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Telegram ada_tg")).toBeInTheDocument();
    expect(within(rows[0]).getByText("09-17 · Europe/London")).toBeInTheDocument();
    expect(within(rows[0]).getAllByRole("cell")[3]).toHaveTextContent(/^Yes$/);
    expect(within(rows[1]).getByText("No email")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Their organization's")).toBeInTheDocument();
  });

  it("lets a long name or email wrap, so a phone keeps each row's Actions on screen", () => {
    renderPage();
    const rows = screen.getAllByTestId("person-row");
    for (const row of rows) {
      const cell = (row as HTMLTableRowElement).cells[0];
      expect(cell.className, "the name cell never holds a long name or email on one line").not.toMatch(/nowrap/);
      expect(cell.className, "the name cell breaks an email with no spaces anywhere").toContain("[overflow-wrap:anywhere]");
    }
  });

  it("shows the disabled people on request, through the hook", async () => {
    renderPage();
    expect(mockDirectory).toHaveBeenLastCalledWith(false);
    await userEvent.click(screen.getByRole("switch", { name: "Show disabled" }));
    expect(mockDirectory).toHaveBeenLastCalledWith(true);
  });

  it("starts a person from a user who signs in, with their sign-in and GitHub login", async () => {
    renderPage();
    await userEvent.click(within(screen.getByTestId("suggested-user")).getByRole("button", { name: "Add Bo" }));
    expect(screen.getByLabelText("Name")).toHaveValue("Bo");
    expect(screen.getByLabelText("Email")).toHaveValue("bo@example.test");
    expect(screen.getByLabelText("Signs in as")).toHaveValue("usr-bo");
    expect(screen.getByLabelText("Identity 1 kind")).toHaveValue("github");
    expect(screen.getByLabelText("Identity 1 value")).toHaveValue("bo-l");
  });

  it("starts a person from an owner login on its own forge", async () => {
    renderPage();
    const owner = screen.getByTestId("suggested-owner");
    expect(within(owner).getByText("Owner of acme/web, acme/api")).toBeInTheDocument();
    await userEvent.click(within(owner).getByRole("button", { name: "Add gitlab cy" }));
    expect(screen.getByLabelText("Identity 1 kind")).toHaveValue("gitlab");
    expect(screen.getByLabelText("Identity 1 value")).toHaveValue("cy");
  });

  it("removes a person after asking", async () => {
    remove.mockImplementation((_id: unknown, opts: { onSuccess: () => void }) => opts.onSuccess());
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Ada" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Remove" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(remove).toHaveBeenCalledWith("per-ada", expect.anything());
  });

  it("offers every change disabled to an operator, who still reads everyone", async () => {
    useAuthStore.setState({ authMode: "session", user: { email: "ops@example.test", name: "Ops", tier: "operator" } });
    renderPage();
    expect(screen.getAllByTestId("person-row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Add person" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add Bo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add gitlab cy" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Ada" }));
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Remove" })).toBeDisabled();
  });

  it("says when everyone embry0 meets is a person", () => {
    mockSuggestions.mockReturnValue(ready({ users: [], owners: [] }));
    renderPage();
    expect(screen.getByText("Everyone embry0 meets is a person.")).toBeInTheDocument();
  });
});

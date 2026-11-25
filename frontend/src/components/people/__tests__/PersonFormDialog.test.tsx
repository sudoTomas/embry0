import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const create = vi.fn();
const update = vi.fn();
vi.mock("@/hooks/usePeopleDirectory", () => ({
  useCreatePerson: () => ({ mutate: create, isPending: false }),
  useUpdatePerson: () => ({ mutate: update, isPending: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), info: vi.fn(), error: vi.fn() } }));

import { PersonFormDialog } from "../PersonFormDialog";
import type { DirectoryPerson } from "@/api/people";

const ADA: DirectoryPerson = {
  id: "per-ada",
  name: "Ada",
  email: "ada@example.test",
  user_id: "usr-ada",
  timezone: null,
  hours: "09-17",
  weekdays: null,
  disabled: false,
  identities: [{ kind: "github", value: "ada-l" }],
  updated_at: "2026-11-25T09:00:00+00:00",
};
const USERS = [{ user_id: "usr-bo", email: "bo@example.test", name: "Bo", github_login: null }];

function refused(status: number, detail: unknown) {
  return (_v: unknown, opts: { onError: (e: unknown) => void }) => opts.onError({ response: { status, data: { detail } } });
}

beforeEach(() => {
  create.mockReset();
  update.mockReset();
});

describe("PersonFormDialog", () => {
  it("adds a person, every field left empty sent as null and each identity trimmed", async () => {
    const onClose = vi.fn();
    create.mockImplementation((_body: unknown, opts: { onSuccess: () => void }) => opts.onSuccess());
    render(<PersonFormDialog users={USERS} onClose={onClose} />);
    await userEvent.type(screen.getByLabelText("Name"), "Cy");
    await userEvent.selectOptions(screen.getByLabelText("Signs in as"), "usr-bo");
    await userEvent.click(screen.getByRole("button", { name: "Add identity" }));
    await userEvent.selectOptions(screen.getByLabelText("Identity 1 kind"), "slack");
    await userEvent.type(screen.getByLabelText("Identity 1 value"), " U024BE7LH ");
    await userEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(create).toHaveBeenCalledWith(
      {
        name: "Cy",
        email: null,
        user_id: "usr-bo",
        timezone: null,
        hours: null,
        weekdays: null,
        disabled: false,
        identities: [{ kind: "slack", value: "U024BE7LH" }],
      },
      expect.anything(),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("changes a person with the version it opened, identities and all", async () => {
    render(<PersonFormDialog person={ADA} users={USERS} onClose={vi.fn()} />);
    expect(screen.getByLabelText("Signs in as")).toHaveValue("usr-ada");
    await userEvent.click(screen.getByRole("button", { name: "Take out identity 1" }));
    await userEvent.clear(screen.getByLabelText("Hours"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    const [{ person, body }] = update.mock.calls[0] as [{ person: DirectoryPerson; body: Record<string, unknown> }];
    expect(person).toBe(ADA);
    expect(body).toMatchObject({ hours: null, identities: [], user_id: "usr-ada" });
  });

  it("says a change someone made meanwhile was refused, and keeps the form open", async () => {
    const onClose = vi.fn();
    update.mockImplementation(refused(409, "the person changed since it was read (now 2026-11-25T10:00:00+00:00): read it again"));
    render(<PersonFormDialog person={ADA} users={USERS} onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Someone changed this person since you opened it");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows each problem the server named, by field", async () => {
    create.mockImplementation(
      refused(422, [
        { loc: ["body", "email"], type: "value_error", msg: "an email address" },
        { loc: ["body", "identities", 0, "value"], type: "value_error", msg: "a GitHub login" },
      ]),
    );
    render(<PersonFormDialog users={USERS} onClose={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Name"), "Cy");
    await userEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(screen.getByRole("alert")).toHaveTextContent("email: an email address");
    expect(screen.getByRole("alert")).toHaveTextContent("identities.0.value: a GitHub login");
  });

  it("asks for a name and for each identity's value before sending anything", async () => {
    render(<PersonFormDialog users={USERS} onClose={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Name the person.");
    await userEvent.type(screen.getByLabelText("Name"), "Cy");
    await userEvent.click(screen.getByRole("button", { name: "Add identity" }));
    await userEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Give each identity a value");
    expect(create).not.toHaveBeenCalled();
  });
});

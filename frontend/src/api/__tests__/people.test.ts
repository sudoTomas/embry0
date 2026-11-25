import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));

import { api } from "../client";
import { createPerson, fetchDirectory, fetchSuggestions, removePerson, updatePerson, type DirectoryPerson } from "../people";

beforeEach(() => vi.clearAllMocks());

const ADA: DirectoryPerson = {
  id: "per/ada",
  name: "Ada",
  email: null,
  user_id: null,
  timezone: null,
  hours: null,
  weekdays: null,
  disabled: false,
  identities: [],
  updated_at: "2026-11-25T09:00:00+00:00",
};
const BODY = { ...ADA, identities: [{ kind: "github" as const, value: "ada-l" }] };

describe("people api", () => {
  it("lists the directory, the disabled too only when asked, and the suggestions", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { people: [ADA], users: [], owners: [] } });
    expect(await fetchDirectory(false)).toEqual([ADA]);
    expect(api.get).toHaveBeenLastCalledWith("/people", { params: {} });
    await fetchDirectory(true);
    expect(api.get).toHaveBeenLastCalledWith("/people", { params: { include_disabled: true } });
    await fetchSuggestions();
    expect(api.get).toHaveBeenLastCalledWith("/people/suggestions");
  });

  it("changes a person under the version it read, and shows refusals itself", async () => {
    (api.patch as ReturnType<typeof vi.fn>).mockResolvedValue({ data: ADA });
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: ADA });
    (api.delete as ReturnType<typeof vi.fn>).mockResolvedValue({});
    const { id: _id, updated_at: _at, ...write } = BODY;
    await updatePerson(ADA, write);
    expect(api.patch).toHaveBeenCalledWith("/people/per%2Fada", write, {
      headers: { "If-Match": '"2026-11-25T09:00:00+00:00"' },
      suppressValidationToast: true,
    });
    await createPerson(write);
    expect(api.post).toHaveBeenCalledWith("/people", write, { suppressValidationToast: true });
    await removePerson("per/ada");
    expect(api.delete).toHaveBeenCalledWith("/people/per%2Fada");
  });
});

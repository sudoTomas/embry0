import { beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));
import { api } from "../client";
import {
  addAgentSkill, createSkillSource, deleteInlineSkill, deleteSkillSource, fetchInlineSkills, fetchSkillSources, fetchSkills,
  putInlineSkill, removeAgentSkill, syncSkillSource, updateSkillSource,
} from "../skills";
import { fetchTools } from "../tools";

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.get).mockResolvedValue({ data: [] }); vi.mocked(api.post).mockResolvedValue({ data: {} }); vi.mocked(api.put).mockResolvedValue({ data: {} }); vi.mocked(api.patch).mockResolvedValue({ data: {} }); vi.mocked(api.delete).mockResolvedValue({ data: {} }); });

describe("skills api paths (docs/api.md, Skills and Tools)", () => {
  it("reads the catalog, sources, inline skills and tools", async () => {
    await fetchSkills(); await fetchSkillSources(); await fetchInlineSkills(); await fetchTools();
    expect(vi.mocked(api.get).mock.calls.map((c) => c[0])).toEqual(["/skills", "/skills/sources", "/skills/inline", "/tools"]);
  });
  it("writes sources", async () => {
    await createSkillSource({ id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "v1" });
    expect(api.post).toHaveBeenCalledWith("/skills/sources", { id: "team", kind: "git", url: "https://example.com/acme/skills.git", ref: "v1" }, { suppressValidationToast: true });
    await updateSkillSource("team", { enabled: false });
    expect(api.patch).toHaveBeenCalledWith("/skills/sources/team", { enabled: false });
    await syncSkillSource("team");
    expect(api.post).toHaveBeenCalledWith("/skills/sources/team/sync");
    await deleteSkillSource("team");
    expect(api.delete).toHaveBeenCalledWith("/skills/sources/team");
  });
  it("writes inline skills and per-agent skills with encoded names", async () => {
    await putInlineSkill("release-notes", { description: "d", body: "# b" });
    expect(api.put).toHaveBeenCalledWith("/skills/inline/release-notes", { description: "d", body: "# b" }, { suppressValidationToast: true });
    await deleteInlineSkill("release-notes");
    expect(api.delete).toHaveBeenCalledWith("/skills/inline/release-notes");
    await addAgentSkill("developer", "superpowers:writing-plans");
    expect(api.post).toHaveBeenCalledWith("/agents/developer/skills/superpowers%3Awriting-plans");
    await removeAgentSkill("developer", "superpowers:writing-plans");
    expect(api.delete).toHaveBeenCalledWith("/agents/developer/skills/superpowers%3Awriting-plans");
  });
});

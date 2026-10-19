import { describe, expect, it } from "vitest";
import { isEmptyDelta, isRepositorySkill, normalizeDelta, skillName } from "../skills";

describe("normalizeDelta", () => {
  it("treats null as the identity", () => expect(normalizeDelta(null)).toEqual({ add: [], remove: [] }));
  it("reads a bare list as replace", () => expect(normalizeDelta(["a:x"])).toEqual({ add: ["a:x"], remove: "*" }));
  it("reads objects by name", () => expect(normalizeDelta({ add: [{ name: "a:x", mode: "autonomous" }] })).toEqual({ add: ["a:x"], remove: [] }));
  it("keeps remove star", () => expect(normalizeDelta({ remove: "*", add: ["b:z"] })).toEqual({ add: ["b:z"], remove: "*" }));
  it("refuses a string", () => expect(() => normalizeDelta("a:x")).toThrow(/skills delta/));
});
describe("helpers", () => {
  it("skillName", () => { expect(skillName("a:x")).toBe("a:x"); expect(skillName({ name: "b:y" })).toBe("b:y"); });
  it("isRepositorySkill", () => { expect(isRepositorySkill("commit-standard")).toBe(true); expect(isRepositorySkill("a:x")).toBe(false); });
  it("isEmptyDelta", () => { expect(isEmptyDelta({ add: [], remove: [] })).toBe(true); expect(isEmptyDelta({ add: [], remove: "*" })).toBe(false); });
});

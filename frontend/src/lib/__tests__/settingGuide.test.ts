import { describe, expect, it } from "vitest";
import { linkedSetting } from "../settingGuide";

describe("linkedSetting", () => {
  it.each([
    ["#advanced-accounts", "accounts"],
    ["#advanced-testSetup", "testSetup"],
    ["#advanced-designStandard", "designStandard"],
  ])("reads %s as the %s section", (hash, setting) => {
    expect(linkedSetting(hash)).toBe(setting);
  });

  it.each(["", "#advanced", "#advanced-", "#history", "#advanced-nothing", "#advanced-constructor", "advanced-accounts"])(
    "names no section for %j",
    (hash) => {
      expect(linkedSetting(hash)).toBeNull();
    },
  );
});

import { describe, it, expect } from "vitest";
import { documentProblems, formatSettingValue, unknownParts } from "../settingsDocument";

function refused(status: number, detail: unknown) {
  return { response: { status, data: { detail } } };
}

describe("documentProblems", () => {
  it("names the key of each problem, in the order the server listed them", () => {
    const problems = documentProblems(
      refused(422, [
        { loc: ["sample", "per_tick"], type: "less_than_equal", msg: "Input should be less than or equal to 20" },
        { loc: ["sample", "enabled"], type: "bool_type", msg: "Input should be a valid boolean" },
        { loc: ["smaple"], type: "unknown_section", msg: "'smaple' is not a settings section. Did you mean 'sample'?" },
      ]),
    );
    expect(problems).toEqual([
      { key: "sample.per_tick", msg: "Input should be less than or equal to 20", type: "less_than_equal" },
      { key: "sample.enabled", msg: "Input should be a valid boolean", type: "bool_type" },
      { key: "smaple", msg: "'smaple' is not a settings section. Did you mean 'sample'?", type: "unknown_section" },
    ]);
  });

  it("gives a YAML syntax error, whose loc is empty, no key", () => {
    expect(documentProblems(refused(422, [{ loc: [], type: "yaml_syntax", msg: "Line 2, column 3: bad indent" }]))).toEqual([
      { key: null, msg: "Line 2, column 3: bad indent", type: "yaml_syntax" },
    ]);
  });

  it("drops pydantic's 'Value error, ' prefix and never reads the rejected input", () => {
    const problems = documentProblems(
      refused(422, [{ loc: ["sample", "hours"], msg: "Value error, hours must be HH-HH", input: "s3cret-value" }]),
    );
    expect(problems).toEqual([{ key: "sample.hours", msg: "hours must be HH-HH" }]);
    expect(JSON.stringify(problems)).not.toContain("s3cret-value");
  });

  it("reads every kind of refusal the same way: by its loc, with its type", () => {
    const problems = documentProblems(
      refused(422, [
        { loc: ["sample", "per_tick"], type: "duplicate_key", msg: "'per_tick' is written twice, on lines 3 and 5; YAML would keep only the second. Keep one." },
        { loc: ["sample"], type: "yaml_alias", msg: "Aliases (*name) are not supported in a settings document." },
        { loc: [], type: "yaml_merge", msg: "Line 4: merge keys (<<) are not supported." },
        { loc: [], type: "too_deep", msg: "The document is nested too deeply." },
        { loc: [], type: "nul_character", msg: "The document cannot hold the NUL character." },
        { loc: ["sample", "hours", 0], type: "check_failed", msg: "The check of this value failed (ValueError)" },
      ]),
    );
    expect(problems).toEqual([
      {
        key: "sample.per_tick",
        msg: "'per_tick' is written twice, on lines 3 and 5; YAML would keep only the second. Keep one.",
        type: "duplicate_key",
      },
      { key: "sample", msg: "Aliases (*name) are not supported in a settings document.", type: "yaml_alias" },
      { key: null, msg: "Line 4: merge keys (<<) are not supported.", type: "yaml_merge" },
      { key: null, msg: "The document is nested too deeply.", type: "too_deep" },
      { key: null, msg: "The document cannot hold the NUL character.", type: "nul_character" },
      { key: "sample.hours.0", msg: "The check of this value failed (ValueError)", type: "check_failed" },
    ]);
  });

  it("keeps the type of a layer's refusal, so the editor can say the key is not settable there", () => {
    const problems = documentProblems(
      refused(422, [
        {
          loc: ["asks", "enabled"],
          type: "not_overridable",
          msg: "'enabled' is the deployment's; an organization can set hours in 'asks'.",
        },
        { loc: ["window"], type: "conflicts_with_inherited", msg: "high must not be below low" },
      ]),
    );
    expect(problems?.map((problem) => problem.type), "a refusal's type was dropped").toEqual([
      "not_overridable",
      "conflicts_with_inherited",
    ]);
  });

  it("reads a string detail as one problem, and is null for anything but a 422", () => {
    expect(documentProblems(refused(422, "If-Match must be the generation"))).toEqual([
      { key: null, msg: "If-Match must be the generation" },
    ]);
    expect(documentProblems(refused(409, "The settings changed"))).toBeNull();
    expect(documentProblems(new Error("boom"))).toBeNull();
    expect(documentProblems(null)).toBeNull();
  });
});

describe("formatSettingValue", () => {
  it("quotes strings and writes everything else as JSON", () => {
    expect(formatSettingValue("09-18")).toBe('"09-18"');
    expect(formatSettingValue(24)).toBe("24");
    expect(formatSettingValue(false)).toBe("false");
    expect(formatSettingValue(null)).toBe("null");
    expect(formatSettingValue(["mon", "tue"])).toBe('["mon","tue"]');
    expect(formatSettingValue(undefined)).toBe("—");
  });
});

describe("unknownParts", () => {
  it("names the sections and keys a newer release wrote, and is null when there are none", () => {
    expect(unknownParts(["later"], [])).toBe("section later");
    expect(unknownParts([], ["sample.extra"])).toBe("key sample.extra");
    expect(unknownParts(["later", "other"], ["sample.extra", "sample.more"])).toBe(
      "sections later, other and keys sample.extra, sample.more",
    );
    expect(unknownParts([], [])).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import {
  CAP_FIELDS,
  auditDays,
  capDraft,
  capProblem,
  changedCaps,
  changedValues,
  draftProblem,
  draftValue,
  initialDraft,
  listEntries,
  scheduleText,
} from "@/lib/contextAuditSettings";
import type { EffectiveSetting } from "@/lib/types/settings";

function setting(value: unknown, source: EffectiveSetting["source"]): EffectiveSetting {
  return {
    key: "k",
    value,
    default: null,
    source,
    set_by: null,
    set_at: null,
    env: null,
    env_still_set: false,
    overridable: ["organization", "repository"],
    refused: null,
    description: null,
  };
}

describe("auditDays", () => {
  it("reads a whole number of days from 1 to 365, and anything else as off", () => {
    expect([7, 1, 365].map(auditDays)).toEqual([7, 1, 365]);
    expect([null, false, 0, 366, 1.5, "7", undefined, true].map(auditDays)).toEqual(Array(8).fill(null));
    expect(scheduleText(1)).toBe("every 1 day");
    expect(scheduleText(false)).toBe("off");
  });
});

describe("a draft and what a save sends", () => {
  it("starts from the organization's own value, or inherits", () => {
    expect(initialDraft("days", setting(7, "organization"))).toEqual({ mode: "every", text: "7" });
    expect(initialDraft("days", setting(false, "organization"))).toEqual({ mode: "off", text: "" });
    expect(initialDraft("days", setting(7, "document")).mode).toBe("inherit");
    expect(initialDraft("open_prs", setting(true, "organization")).mode).toBe("on");
    expect(initialDraft("files", setting(["a/*.md", "b.md"], "organization"))).toEqual({ mode: "own", text: "a/*.md\nb.md" });
    expect(initialDraft("exclude_dirs", setting(["vendor", "dist"], "organization")).text).toBe("vendor, dist");
    expect(initialDraft("files", setting(null, "organization")).mode).toBe("inherit");
  });

  it("writes false to turn the schedule off and null to inherit", () => {
    expect(draftValue("days", { mode: "off", text: "" })).toBe(false);
    expect(draftValue("days", { mode: "inherit", text: "7" })).toBeNull();
    expect(draftValue("days", { mode: "every", text: " 30 " })).toBe(30);
    expect(draftValue("exclude_dirs", { mode: "own", text: "vendor,\n dist ,," })).toEqual(["vendor", "dist"]);
    expect(listEntries("a\n\n b ", false)).toEqual(["a", "b"]);
  });

  it("sends only what differs from what the layer holds", () => {
    const settings = {
      days: setting(7, "organization"),
      open_prs: setting(false, "default"),
      files: setting(null, "default"),
      exclude_dirs: setting(null, "default"),
    };
    const drafts = {
      days: { mode: "every", text: "7" },
      open_prs: { mode: "inherit", text: "" },
      files: { mode: "inherit", text: "" },
      exclude_dirs: { mode: "own", text: "vendor" },
    };
    expect(changedValues(drafts, settings)).toEqual({ exclude_dirs: ["vendor"] });
    expect(changedValues({ ...drafts, days: { mode: "inherit", text: "" } }, settings)).toEqual({
      days: null,
      exclude_dirs: ["vendor"],
    });
  });

  it("refuses a schedule out of range and an empty list of globs before asking", () => {
    expect(draftProblem("days", { mode: "every", text: "0" })).toMatch(/from 1 to 365/);
    expect(draftProblem("days", { mode: "every", text: "1.5" })).toMatch(/from 1 to 365/);
    expect(draftProblem("days", { mode: "every", text: "365" })).toBeNull();
    expect(draftProblem("files", { mode: "own", text: " \n" })).toMatch(/at least one glob/);
    expect(draftProblem("exclude_dirs", { mode: "own", text: "" })).toBeNull();
  });
});

describe("the caps", () => {
  const [perAudit, perWake] = CAP_FIELDS;

  it("checks a cap before asking: dollars more than 0, a count from 0 to 50", () => {
    expect(capProblem(perAudit, { mode: "own", text: "0" })).toMatch(/more than 0/);
    expect(capProblem(perAudit, { mode: "own", text: "0.25" })).toBeNull();
    expect(capProblem(perWake, { mode: "own", text: "0" })).toBeNull();
    expect(capProblem(perWake, { mode: "own", text: "51" })).toMatch(/0 to 50/);
    expect(capProblem(perWake, { mode: "own", text: "1.5" })).toMatch(/0 to 50/);
    expect(capProblem(perWake, { mode: "inherit", text: "" })).toBeNull();
  });

  it("sends a number for an own cap and null to inherit, only where it changes", () => {
    const settings = {
      context_audit_job_usd: setting(5, "organization"),
      context_audit_max_jobs_per_tick: setting(3, "default"),
      fix_job_usd: setting(2, "document"),
    };
    const drafts = Object.fromEntries(CAP_FIELDS.map((f) => [f.key, capDraft(settings[f.key])]));
    expect(changedCaps(drafts, settings)).toEqual({});
    expect(changedCaps({ ...drafts, context_audit_job_usd: { mode: "inherit", text: "5" } }, settings)).toEqual({
      context_audit_job_usd: null,
    });
    expect(changedCaps({ ...drafts, fix_job_usd: { mode: "own", text: "0.5" } }, settings)).toEqual({
      fix_job_usd: 0.5,
    });
  });
});

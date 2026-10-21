import { describe, expect, it } from "vitest";
import type { RepoStandardState } from "@/api/standards";
import { standard, state } from "@/components/standards/__tests__/fixtures";
import {
  auditBlockedReason,
  colourRoles,
  formatRoleValue,
  isOffAndUnscored,
  isPlainColour,
  modeCounts,
  nextAuditRepo,
  otherRoles,
  requestErrorText,
  scoreTextClass,
  scoreTone,
  standardRows,
  standardTitle,
  typeScale,
  type StandardRepoRow,
} from "../standards";

const row = (repo: string, overrides: Partial<RepoStandardState> = {}): StandardRepoRow => ({ repo, state: state(repo.toLowerCase(), overrides) });

describe("standardRows", () => {
  it("lists every registered repository under the owner, matching stored states without case", () => {
    const rows = standardRows(
      "acme",
      "design",
      [{ repo: "acme/Widgets" }, { repo: "acme/gears" }, { repo: "other/thing" }],
      [state("acme/widgets", { mode: "advise", score: 40 })],
    );
    expect(rows.map((r) => r.repo)).toEqual(["acme/Widgets", "acme/gears"]);
    // The registry's spelling is kept: the mode and audit routes look it up.
    expect(rows[0].state.mode).toBe("advise");
    expect(rows[1].state.mode).toBe("off");
  });

  it("keeps a stored state whose repository the registry list left out", () => {
    const rows = standardRows("acme", "design", [], [state("acme/retired", { mode: "advise" })]);
    expect(rows.map((r) => r.repo)).toEqual(["acme/retired"]);
  });

  it("sorts by score ascending, no score last, then enforce, advise, off, then name", () => {
    const rows = standardRows(
      "acme",
      "design",
      ["acme/b-off", "acme/a-off", "acme/advise", "acme/enforce", "acme/high", "acme/low"].map((repo) => ({ repo })),
      [
        state("acme/high", { mode: "advise", score: 90 }),
        state("acme/low", { mode: "advise", score: 12 }),
        state("acme/advise", { mode: "advise" }),
        state("acme/enforce", { mode: "enforce" }),
      ],
    );
    expect(rows.map((r) => r.repo)).toEqual(["acme/low", "acme/high", "acme/enforce", "acme/advise", "acme/a-off", "acme/b-off"]);
  });

  it("ignores a state of another kind", () => {
    const rows = standardRows("acme", "design", [{ repo: "acme/a" }], [state("acme/a", { kind: "security", mode: "advise" })]);
    expect(rows[0].state.mode).toBe("off");
  });
});

describe("audits", () => {
  const ctx = { hasStandard: true, canAudit: true, jobStatus: undefined };

  it("says why a repository cannot be audited", () => {
    expect(auditBlockedReason(row("acme/a", { mode: "advise" }), ctx)).toBeNull();
    expect(auditBlockedReason(row("acme/a"), ctx)).toMatch(/mode off/);
    expect(auditBlockedReason(row("acme/a", { mode: "advise" }), { ...ctx, hasStandard: false })).toMatch(/no active standard/);
    expect(auditBlockedReason(row("acme/a", { mode: "advise" }), { ...ctx, canAudit: false })).toMatch(/operator/);
    expect(auditBlockedReason(row("acme/a", { mode: "enforce" }), { ...ctx, jobStatus: "running" })).toBe(
      "An audit is already running.",
    );
    expect(auditBlockedReason(row("acme/a", { mode: "enforce" }), { ...ctx, jobStatus: "pending" })).toBe(
      "An audit is already pending.",
    );
    // A finished audit, failed or not, blocks nothing.
    expect(auditBlockedReason(row("acme/a", { mode: "advise" }), { ...ctx, jobStatus: "failed" })).toBeNull();
    expect(auditBlockedReason(row("acme/a", { mode: "advise" }), { ...ctx, jobStatus: "completed" })).toBeNull();
  });

  it("gilds the first never-audited repository under the standard that can start, or none", () => {
    const rows = [
      row("acme/scored", { mode: "advise", score: 30, last_audit_at: "2026-01-01T00:00:00Z" }),
      row("acme/busy", { mode: "advise", last_audit_job_id: "job-1" }),
      row("acme/next", { mode: "enforce" }),
      row("acme/later", { mode: "advise" }),
      row("acme/off"),
    ];
    const blocked = (r: StandardRepoRow) => (r.repo === "acme/busy" ? "busy" : r.state.mode === "off" ? "off" : null);
    expect(nextAuditRepo(rows, blocked)).toBe("acme/next");
    expect(nextAuditRepo(rows.slice(0, 2), blocked)).toBeNull();
    expect(nextAuditRepo([row("acme/off")], () => null)).toBeNull();
  });

  it("folds away only a repository in mode off that no audit scored", () => {
    expect(isOffAndUnscored(row("acme/a"))).toBe(true);
    expect(isOffAndUnscored(row("acme/a", { score: 40, last_audit_at: "2026-01-01T00:00:00Z" }))).toBe(false);
    expect(isOffAndUnscored(row("acme/a", { mode: "advise" }))).toBe(false);
    expect(isOffAndUnscored(row("acme/a", { mode: "enforce" }))).toBe(false);
  });

  it("counts the modes", () => {
    expect(modeCounts([row("a", { mode: "advise" }), row("b"), row("c"), row("d", { mode: "enforce" })])).toEqual({
      off: 2,
      advise: 1,
      enforce: 1,
    });
  });

  it("tints a score by how far it is from the standard", () => {
    expect(scoreTone(null)).toBeNull();
    expect(scoreTone(80)).toBe("success");
    expect(scoreTone(79)).toBe("warning");
    expect(scoreTone(50)).toBe("warning");
    expect(scoreTone(49)).toBe("error");
    expect(scoreTextClass(null)).toBe("");
    expect(scoreTextClass(10)).toBe("text-destructive");
  });

  it("reads the API's own words first", () => {
    expect(requestErrorText({ response: { status: 409, data: { detail: "acme/a is in mode off" } } }, "x")).toBe(
      "acme/a is in mode off",
    );
    expect(requestErrorText({ response: { status: 403, data: {} } }, "x")).toMatch(/tier/);
    expect(requestErrorText({}, "fallback")).toBe("fallback");
  });
});

describe("the standard's parts", () => {
  it("splits the roles into colours and the rest, by name, with their values", () => {
    const s = standard();
    expect(colourRoles(s).map((r) => [r.name, r.value])).toEqual([
      ["color.accent", "#3366ff"],
      ["color.base", "#101014"],
    ]);
    expect(otherRoles(s).map((r) => r.name)).toEqual(["font.body", "radius"]);
    expect(otherRoles(s)[0].type).toBe("font-family");
  });

  it("marks a role the values leave out", () => {
    const [accent] = colourRoles(standard({ values: {} }));
    expect(accent.hasValue).toBe(false);
  });

  it("formats a font stack as a list and anything else as JSON", () => {
    expect(formatRoleValue(["Inter", "system-ui"])).toBe("Inter, system-ui");
    expect(formatRoleValue(8)).toBe("8");
    expect(formatRoleValue({ a: 1 })).toBe('{"a":1}');
    expect(formatRoleValue(null)).toBe("");
  });

  it("paints only a plain colour", () => {
    for (const ok of ["#fff", "#101014", "#10101480", "rgba(255, 255, 255, 0.92)", "hsl(10 20% 30% / .5)", "transparent"]) {
      expect(isPlainColour(ok), ok).toBe(true);
    }
    for (const bad of ["url(https://example.com/x.png)", "red; background: url(x)", "var(--x)", 12, ["#fff"], "#12"]) {
      expect(isPlainColour(bad), String(bad)).toBe(false);
    }
  });

  it("orders the type scale largest first and keeps the other keys as words", () => {
    const steps = typeScale(standard().schema);
    expect(steps.map((s) => s.name)).toEqual(["h1", "body", "th"]);
    expect(steps[2]).toEqual({ name: "th", size: "12px", weight: "600", other: "transform: uppercase · letter spacing: 0.06em" });
    expect(typeScale({})).toEqual([]);
  });

  it("titles a kind", () => {
    expect(standardTitle("design")).toBe("Design standard");
  });
});

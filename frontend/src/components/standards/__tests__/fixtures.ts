import type { OwnerStandards, RepoStandardState, Standard } from "@/api/standards";

/** A neutral two-colour standard; the values are placeholders, not a recommendation. */
export function standard(overrides: Partial<Standard> = {}): Standard {
  return {
    id: "std-1",
    owner: "acme",
    kind: "design",
    version: 2,
    status: "active",
    schema: {
      roles: {
        "color.base": { type: "color", description: "page background" },
        "color.accent": { type: "color", description: "the one accent" },
        "font.body": { type: "font-family" },
        radius: { type: "length", description: "cards and tables" },
      },
      type_scale: {
        body: { size: "14px", weight: 400 },
        h1: { size: "20px", weight: 700 },
        th: { size: "12px", weight: 600, transform: "uppercase", letter_spacing: "0.06em" },
      },
      rules: [
        { id: "one-accent", text: "One accent colour." },
        { id: "tables-first", text: "Lists of records are tables." },
      ],
    },
    values: {
      "color.base": "#101014",
      "color.accent": "#3366ff",
      "font.body": ["Inter", "system-ui", "sans-serif"],
      radius: "8px",
    },
    prose: "Dense and dark.",
    references: ["https://example.com/brand", "acme/design-system"],
    created_by: "admin@example.com",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

export function state(repo: string, overrides: Partial<RepoStandardState> = {}): RepoStandardState {
  return {
    repo,
    owner: "acme",
    kind: "design",
    mode: "off",
    last_audit_at: null,
    last_audit_job_id: null,
    score: null,
    findings_open: 0,
    exceptions: [],
    updated_at: null,
    ...overrides,
  };
}

export function ownerStandards(overrides: Partial<OwnerStandards> = {}): OwnerStandards {
  return {
    owner: "acme",
    kinds: ["design"],
    modes: ["off", "advise", "enforce"],
    standards: [standard()],
    repos: [],
    ...overrides,
  };
}

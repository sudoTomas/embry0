import type { OrgAccess } from "@/api/orgs";
import { Badge, type BadgeTone } from "@/components/ui/Badge";

const GRANTS = [
  { key: "read", label: "Read", yes: "can list repositories", no: "cannot list repositories" },
  { key: "hooks", label: "Hooks", yes: "can manage webhooks", no: "cannot manage webhooks" },
  { key: "push", label: "Push", yes: "can push", no: "cannot push" },
] as const;

/**
 * The read / hooks / push grants a probe learned. `null` is "not probed",
 * never "no": push in particular is only proven by writing a ref, which the
 * probe does not do.
 */
export function AccessChips({ access }: { access: Partial<OrgAccess> | null | undefined }) {
  if (!access || access.read === undefined) {
    return (
      <Badge tone="neutral" title="No probe has run for this organization">
        Not probed
      </Badge>
    );
  }
  return (
    <span className="inline-flex flex-wrap gap-1" data-testid="access-chips">
      {GRANTS.map((g) => {
        const value = access[g.key];
        const state = value === true ? "yes" : value === false ? "no" : "unknown";
        const tone: BadgeTone = state === "yes" ? "success" : state === "no" ? "error" : "neutral";
        const title = state === "yes" ? g.yes : state === "no" ? g.no : `${g.label}: not probed`;
        return (
          <span key={g.key} data-testid={`access-${g.key}`} data-state={state}>
            <Badge tone={tone} title={title}>
              {state === "no" ? `No ${g.label.toLowerCase()}` : state === "unknown" ? `${g.label}?` : g.label}
            </Badge>
          </span>
        );
      })}
    </span>
  );
}

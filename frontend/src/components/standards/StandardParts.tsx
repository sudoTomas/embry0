import type { CSSProperties } from "react";
import type { Standard, StandardRule } from "@/api/standards";
import { Badge } from "@/components/ui/Badge";
import { TABLE_BOX } from "@/components/orgs/styles";
import { formatRoleValue, isPlainColour, typeScale, type RoleEntry, type TypeStep } from "@/lib/standards";
import { statusLabel, statusTone } from "@/lib/status";
import { actorLabel } from "@/lib/actors";
import { formatDate } from "@/lib/utils";

/**
 * The parts of one standard, read-only: its colour roles as swatches, its
 * other roles, its type scale, its rules and its versions. The JSON is
 * written through the API, never here.
 */

function Muted({ children }: { children: string }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

/** Each colour role with a swatch of its value, the value in mono, and what the role is for. */
export function ColourRoles({ roles }: { roles: readonly RoleEntry[] }) {
  if (roles.length === 0) return <Muted>The schema declares no colour roles.</Muted>;
  return (
    <ul
      className="grid grid-cols-1 gap-x-4 gap-y-1 rounded-lg border border-border p-2 sm:grid-cols-2 xl:grid-cols-3"
      data-testid="standard-colours"
    >
      {roles.map((role) => {
        const paint = isPlainColour(role.value);
        return (
          <li key={role.name} className="flex min-w-0 items-center gap-3 rounded-md px-1.5 py-1.5" data-testid="standard-colour">
            <span
              aria-hidden="true"
              className="h-8 w-8 shrink-0 rounded-md ring-1 ring-inset ring-border-strong"
              style={paint ? { background: role.value as string } : undefined}
              data-testid="standard-swatch"
              data-painted={paint ? "true" : "false"}
            />
            <div className="min-w-0 leading-tight">
              <div className="truncate font-mono text-xs text-fg">{role.name}</div>
              <div className="truncate font-mono text-xs text-fg-secondary" title={formatRoleValue(role.value)}>
                {role.hasValue ? formatRoleValue(role.value) : "no value"}
              </div>
              {role.description && <div className="truncate text-2xs text-fg-muted" title={role.description}>{role.description}</div>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** The roles that are not colours: fonts, lengths, easings, with their values in mono. */
export function OtherRoles({ roles }: { roles: readonly RoleEntry[] }) {
  if (roles.length === 0) return <Muted>The schema declares no other roles.</Muted>;
  return (
    <div className={TABLE_BOX}>
      <table className="data-table" data-testid="standard-other-roles">
        <thead>
          <tr>
            <th scope="col">Role</th>
            <th scope="col">Value</th>
            <th scope="col" className="hidden sm:table-cell">For</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.name}>
              <td className="whitespace-nowrap font-mono text-xs">{role.name}</td>
              <td className="font-mono text-xs text-fg-secondary">
                {role.hasValue ? formatRoleValue(role.value) : <span className="text-fg-muted">no value</span>}
              </td>
              <td className="hidden text-xs text-fg-muted sm:table-cell">{role.description ?? role.type ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const PX = /^\d{1,2}(?:\.\d+)?px$/;
const WEIGHT = /^[1-9]00$/;

/** A sample of the step in its own size and weight, when both are plain values. */
function sampleStyle(step: TypeStep): CSSProperties | undefined {
  if (!PX.test(step.size)) return undefined;
  return { fontSize: step.size, fontWeight: WEIGHT.test(step.weight) ? Number(step.weight) : undefined };
}

/** The type steps, largest first, with a sample of each. */
export function TypeScaleTable({ standard }: { standard: Pick<Standard, "schema"> }) {
  const steps = typeScale(standard.schema);
  if (steps.length === 0) return <Muted>The schema declares no type scale.</Muted>;
  return (
    <div className={TABLE_BOX}>
      <table className="data-table" data-testid="standard-type-scale">
        <thead>
          <tr>
            <th scope="col">Step</th>
            <th scope="col" className="num">Size</th>
            <th scope="col" className="num">Weight</th>
            <th scope="col" className="hidden sm:table-cell">Sample</th>
            <th scope="col" className="hidden md:table-cell">Also</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((step) => (
            <tr key={step.name} data-testid="standard-type-step">
              <td className="whitespace-nowrap font-mono text-xs">{step.name}</td>
              <td className="num whitespace-nowrap font-mono text-xs">{step.size || "-"}</td>
              <td className="num font-mono text-xs">{step.weight || "-"}</td>
              <td className="hidden whitespace-nowrap leading-none sm:table-cell" style={sampleStyle(step)}>
                Aa
              </td>
              <td className="hidden text-xs text-fg-muted md:table-cell">{step.other}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The rules in words, each under its id. */
export function RulesList({ rules }: { rules: readonly StandardRule[] | undefined }) {
  if (!rules || rules.length === 0) return <Muted>The schema declares no rules.</Muted>;
  return (
    <ol className="divide-y divide-border rounded-lg border border-border" data-testid="standard-rules">
      {rules.map((rule) => (
        <li key={rule.id} className="flex flex-col gap-0.5 px-3 py-2 sm:flex-row sm:gap-4" data-testid="standard-rule">
          <span className="shrink-0 font-mono text-xs text-fg-muted sm:w-44">{rule.id}</span>
          <span className="text-sm text-fg-secondary">{rule.text}</span>
        </li>
      ))}
    </ol>
  );
}

/** Every version, newest first. */
export function VersionsTable({ versions }: { versions: readonly Standard[] }) {
  if (versions.length === 0) return <Muted>No versions.</Muted>;
  return (
    <div className={TABLE_BOX}>
      <table className="data-table" data-testid="standard-versions">
        <thead>
          <tr>
            <th scope="col" className="num">Version</th>
            <th scope="col">Status</th>
            <th scope="col" className="hidden sm:table-cell">Written by</th>
            <th scope="col">Written</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.id} data-testid="standard-version-row">
              <td className="num w-px font-mono text-xs">{v.version}</td>
              <td className="w-px">
                <Badge tone={statusTone(v.status)}>{statusLabel(v.status)}</Badge>
              </td>
              <td className="hidden font-mono text-xs text-fg-secondary sm:table-cell">{actorLabel(v.created_by)}</td>
              <td className="whitespace-nowrap text-fg-muted">{v.created_at ? formatDate(v.created_at) : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

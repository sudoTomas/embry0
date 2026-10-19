import type { ToolCatalogEntry } from "@/lib/types";

/** Offered while `GET /tools` is loading or empty, so the form never offers zero tools. */
const FALLBACK_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"];

interface ToolChecklistProps {
  value: string[];
  onToggle: (tool: string) => void;
  catalog: ToolCatalogEntry[];
}

interface ToolGroup {
  label: string | null;
  names: string[];
}

/** Catalog grouped by source (builtin first, the rest alphabetically), then the agent's other tools. */
function groupTools(catalog: ToolCatalogEntry[], value: string[]): ToolGroup[] {
  const groups: ToolGroup[] = [];
  if (catalog.length === 0) {
    groups.push({ label: null, names: FALLBACK_TOOLS });
  } else {
    const bySource = new Map<string, string[]>();
    for (const t of catalog) bySource.set(t.source, [...(bySource.get(t.source) ?? []), t.name]);
    const sources = [...bySource.keys()].sort((a, b) =>
      a === "builtin" ? -1 : b === "builtin" ? 1 : a.localeCompare(b),
    );
    for (const s of sources) groups.push({ label: s, names: bySource.get(s)! });
  }
  const known = new Set(groups.flatMap((g) => g.names));
  const other = value.filter((t) => !known.has(t));
  if (other.length > 0) groups.push({ label: "Other", names: other });
  return groups;
}

export function ToolChecklist({ value, onToggle, catalog }: ToolChecklistProps) {
  return (
    <div className="mt-2 space-y-3">
      {groupTools(catalog, value).map((group) => (
        <div key={group.label ?? "default"}>
          {group.label && <p className="mb-1 text-[10px] uppercase text-white/40">{group.label}</p>}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {group.names.map((tool) => (
              <label
                key={tool}
                className="flex items-center gap-2.5 px-3 py-2 rounded-md border border-white/[0.06] bg-white/[0.02] cursor-pointer hover:bg-white/[0.04] transition-colors"
              >
                <input
                  type="checkbox"
                  checked={value.includes(tool)}
                  onChange={() => onToggle(tool)}
                  className="w-4 h-4 rounded accent-cyan-500"
                />
                <span className="text-sm text-white/70 font-mono break-all">{tool}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

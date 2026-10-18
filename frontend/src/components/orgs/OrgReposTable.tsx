import { useMemo, useState } from "react";
import { Link } from "react-router";
import { FolderGit2 } from "lucide-react";
import type { OrgRepoFacts } from "@/api/orgs";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { SENSES, isMissing, senseState, senseTitle, type SenseKey, type SenseState } from "@/lib/orgs";
import { cn, formatDate } from "@/lib/utils";
import { TABLE_BOX, TD, TH } from "./styles";

const CHIP: Record<SenseState, string> = {
  on: "border-success/25 bg-success/10 text-success",
  off: "border-white/10 bg-white/[0.02] text-white/30 line-through",
  unknown: "border-warning/25 bg-warning/10 text-warning",
};

function SenseChips({ row }: { row: OrgRepoFacts }) {
  if (row.archived) return <Badge tone="neutral">archived</Badge>;
  return (
    <span className="inline-flex gap-1 whitespace-nowrap">
      {SENSES.map((sense) => {
        const state = senseState(row, sense);
        return (
          <span
            key={sense.key}
            data-testid={`sense-${sense.key}`}
            data-state={state}
            title={senseTitle(sense, state, row)}
            className={cn(
              "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
              CHIP[state],
            )}
          >
            {state === "unknown" ? `${sense.label}?` : sense.label}
          </span>
        );
      })}
    </span>
  );
}

/**
 * Every repository discovery found under an owner, one chip per sense, with
 * a text filter and a filter for repositories missing one sense.
 */
export function OrgReposTable({ repos }: { repos: readonly OrgRepoFacts[] }) {
  const [text, setText] = useState("");
  const [missing, setMissing] = useState<SenseKey | "">("");

  const shown = useMemo(() => {
    const needle = text.trim().toLowerCase();
    return repos.filter(
      (r) =>
        (!needle || r.repo.toLowerCase().includes(needle) || (r.language ?? "").toLowerCase().includes(needle)) &&
        (!missing || isMissing(r, missing)),
    );
  }, [repos, text, missing]);

  if (repos.length === 0) {
    return (
      <EmptyState
        icon={FolderGit2}
        title="No repositories yet"
        description="Discovery has not stored any repositories for this organization. Run it to list them."
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Filter by name or language"
          aria-label="Filter repositories"
          className="h-8 w-full text-xs sm:w-64"
          data-testid="org-repos-filter"
        />
        <select
          value={missing}
          onChange={(e) => setMissing(e.target.value as SenseKey | "")}
          aria-label="Missing sense"
          className="h-8 rounded-md border border-input bg-background px-2 text-xs"
          data-testid="org-repos-missing"
        >
          <option value="">Any sense</option>
          {SENSES.map((s) => (
            <option key={s.key} value={s.key}>
              Missing {s.label}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground" data-testid="org-repos-count">
          {shown.length} of {repos.length}
        </span>
      </div>
      <div className={TABLE_BOX}>
        <table className="w-full min-w-[900px] text-sm" data-testid="org-repos-table">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th scope="col" className={TH}>Repository</th>
              <th scope="col" className={TH}>Language</th>
              <th scope="col" className={TH}>Pushed</th>
              <th scope="col" className={cn(TH, "text-right")}>Issues</th>
              <th scope="col" className={TH}>Senses</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.repo} className="border-b border-white/[0.04] last:border-0" data-testid="org-repo-row">
                <td className={TD}>
                  <div className="flex flex-col gap-0.5">
                    <Link to={`/repos/${r.repo}`} className="font-medium hover:underline" data-testid="org-repo-link">
                      {r.repo}
                    </Link>
                    {r.maintain_hint && (
                      <span className="max-w-md text-xs text-muted-foreground" data-testid="org-repo-hint">
                        {r.maintain_hint}
                      </span>
                    )}
                    {r.facts.registered === true && (
                      <Link
                        to={`/qa/repos/${encodeURIComponent(r.repo)}/config`}
                        className="text-xs text-white/50 hover:underline"
                        data-testid="org-repo-qa-link"
                      >
                        QA config
                      </Link>
                    )}
                  </div>
                </td>
                <td className={cn(TD, "text-white/70")}>{r.language ?? "-"}</td>
                <td className={cn(TD, "whitespace-nowrap text-white/60")}>
                  {r.pushed_at ? formatDate(r.pushed_at) : "-"}
                </td>
                <td className={cn(TD, "text-right font-mono text-white/70")}>{r.open_issues ?? "-"}</td>
                <td className={TD}>
                  <SenseChips row={r} />
                </td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={5} className={cn(TD, "py-6 text-center text-sm text-muted-foreground")}>
                  No repository matches these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

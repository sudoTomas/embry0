import type { ReactNode } from "react";
import { Link } from "react-router";
import { DashboardPanel } from "@/components/dashboard/DashboardPanel";
import { sentenceCase } from "@/lib/status";
import { formatCost, formatTokens } from "@/lib/utils";
import type { StatsResponse } from "@/lib/types";

/** How an expensive issue is named: owner/repo#42 on GitHub, the repository alone elsewhere. */
function issueKey(issue: { repo: string; issue_number: number }): string {
  return issue.issue_number > 0 ? `${issue.repo}#${issue.issue_number}` : issue.repo;
}

function FailureBars({ categories }: { categories: Record<string, number> }) {
  const rows = Object.entries(categories)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const max = rows[0]?.[1] ?? 0;
  return (
    <ul className="space-y-2 px-3 py-3">
      {rows.map(([category, n]) => {
        const label = sentenceCase(category);
        return (
          <li key={category} className="space-y-1 text-sm">
            <div className="flex items-baseline justify-between">
              <span className="text-fg-secondary">{label}</span>
              <span className="tabular-nums text-fg">{n}</span>
            </div>
            <div
              role="meter"
              aria-label={label}
              aria-valuenow={n}
              aria-valuemin={0}
              aria-valuemax={max}
              className="h-1.5 w-full overflow-hidden rounded-full bg-white/5"
            >
              <div className="h-full rounded-full bg-fg-muted" style={{ width: `${max > 0 ? (n / max) * 100 : 0}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Row({ label, value, title }: { label: string; value: ReactNode; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-3 py-1.5 text-sm">
      <dt className="text-fg-secondary">{label}</dt>
      <dd className="text-right tabular-nums text-fg" title={title}>
        {value}
      </dd>
    </div>
  );
}

/**
 * The rest of /stats (spec 3.7, "Deep stats"), under the Overview's tiles and
 * panels: failures by category, cost by repository, tokens by agent, the most
 * expensive issues, and one small list for the fleet (queue depth, pre-built
 * QA images, the credential registry). A field a backend does not send, or
 * sends null, leaves its panel empty or its row out; nothing here is gold.
 */
export function DeepStats({ stats }: { stats: StatsResponse }) {
  const failures = Object.values(stats.failure_categories ?? {}).some((n) => n > 0);
  const costByRepo = stats.cost_by_repo ?? [];
  const tokens = stats.tokens_by_agent ?? [];
  const expensive = stats.top_expensive_issues ?? [];
  const images = stats.qa_prebuilt_images ?? null;
  const credentials = stats.credentials ?? null;

  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2" data-testid="deep-stats">
      <DashboardPanel title="Failures by category" isPending={false} isError={false} isEmpty={!failures} emptyText="No failures recorded.">
        <FailureBars categories={stats.failure_categories ?? {}} />
      </DashboardPanel>

      <DashboardPanel title="Fleet" isPending={false} isError={false} isEmpty={false} emptyText="">
        <dl className="divide-y divide-border py-1">
          <Row label="Queue depth" value={stats.queue_depth} />
          {images && (
            <Row
              label="QA images"
              value={`${images.fresh} fresh · ${images.stale.length} stale · ${images.unknown.length} unknown`}
              title={images.stale.length > 0 ? `Stale: ${images.stale.map((i) => i.repo).join(", ")}` : undefined}
            />
          )}
          {credentials && (
            <Row
              label="Credentials"
              value={`${credentials.count} · ${credentials.quarantined} quarantined · ${credentials.disabled} disabled${
                credentials.env_fallback_keys.length > 0 ? ` · env fallback: ${credentials.env_fallback_keys.join(", ")}` : ""
              }`}
            />
          )}
        </dl>
      </DashboardPanel>

      <DashboardPanel title="Cost by repository" isPending={false} isError={false} isEmpty={costByRepo.length === 0} emptyText="No jobs with a cost yet.">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col" className="num">Jobs</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {costByRepo.map((row) => (
              <tr key={row.repo}>
                <td className="w-full max-w-0 truncate font-mono text-xs">{row.repo}</td>
                <td className="num tabular-nums text-fg-secondary">{row.job_count}</td>
                <td className="num tabular-nums">{formatCost(row.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>

      <DashboardPanel title="Tokens by agent" isPending={false} isError={false} isEmpty={tokens.length === 0} emptyText="No agent runs recorded yet.">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col" className="num">Runs</th>
              <th scope="col" className="num hidden sm:table-cell">Input</th>
              <th scope="col" className="num hidden sm:table-cell">Output</th>
              <th scope="col" className="num">Cache hits</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {tokens.map((row) => (
              <tr key={row.agent_type}>
                <td className="w-full">{sentenceCase(row.agent_type)}</td>
                <td className="num tabular-nums text-fg-secondary">{row.runs}</td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{formatTokens(row.input_tokens)}</td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{formatTokens(row.output_tokens)}</td>
                <td className="num tabular-nums">{`${Math.round(row.cache_hit_rate * 100)}%`}</td>
                <td className="num tabular-nums">{formatCost(row.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>

      <DashboardPanel
        title="Most expensive issues"
        viewAll={{ to: "/issues", label: "All issues" }}
        isPending={false}
        isError={false}
        isEmpty={expensive.length === 0}
        emptyText="No issues with a cost yet."
      >
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Issue</th>
              <th scope="col" className="num hidden sm:table-cell">Jobs</th>
              <th scope="col" className="num">Cost</th>
            </tr>
          </thead>
          <tbody>
            {expensive.map((issue) => (
              <tr key={issue.trace_id}>
                <td className="w-full max-w-0">
                  <Link to={`/issues/${issue.trace_id}`} className="block truncate hover:underline" title={issue.title}>
                    <span className="font-mono text-xs text-fg-secondary">{issueKey(issue)}</span>
                    {issue.title && <span className="ml-2">{issue.title}</span>}
                  </Link>
                </td>
                <td className="num hidden tabular-nums text-fg-secondary sm:table-cell">{issue.job_count}</td>
                <td className="num tabular-nums">{formatCost(issue.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DashboardPanel>
    </div>
  );
}

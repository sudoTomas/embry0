import { useEffect, useMemo, useState } from "react";
import { ScrollText } from "lucide-react";
import { Link } from "react-router";
import { Button } from "@/components/ui/Button";
import { useAuditActions } from "@/hooks/useAuditActions";
import { useAuditLog } from "@/hooks/useAuditLog";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Pagination } from "@/components/ui/Pagination";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { formatDateTime } from "@/lib/utils";
import { formatDetails } from "@/lib/auditLabels";

const WINDOW_OPTIONS = [
  { hours: 0, label: "All time" },
  { hours: 24, label: "Last 24 h" },
  { hours: 24 * 7, label: "Last 7 days" },
  { hours: 24 * 30, label: "Last 30 days" },
];

const LIMIT = 50;

/**
 * Global audit log (RAV-1056 Phase C). Admin sessions see every row;
 * operator/read callers reaching this page by URL see only their own —
 * the backend enforces row scoping regardless of nav visibility. The
 * action filter and each row's words come from the action registry
 * (GET /audit/actions), so an action added there is selectable here
 * without a dashboard change. The log does not wait for the registry: a
 * row reads in sentence case until it is read, and if it cannot be, the
 * page says so above the rows and keeps showing them.
 */
export function AuditPage() {
  const [offset, setOffset] = useState(0);
  const [actionFilter, setActionFilter] = useState<string | undefined>();
  const [actorInput, setActorInput] = useState("");
  const [actorFilter, setActorFilter] = useState<string | undefined>();
  const [windowHours, setWindowHours] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setActorFilter(actorInput.trim() || undefined);
      setOffset(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [actorInput]);

  const since = useMemo(
    () => (windowHours > 0 ? new Date(Date.now() - windowHours * 3_600_000).toISOString() : undefined),
    [windowHours],
  );
  const filters = useMemo(
    () => ({ actor: actorFilter, action: actionFilter, since, limit: LIMIT, offset }),
    [actorFilter, actionFilter, since, offset],
  );
  const audit = useAuditLog(filters);
  const actions = useAuditActions();
  const { vocabulary } = actions;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <ScrollText size={18} className="text-fg-muted" />
        <h1 className="title-page text-xl font-bold">Audit</h1>
        {audit.data && <span className="text-sm text-muted-foreground">{audit.data.total}</span>}
      </div>
      <p className="text-sm text-muted-foreground">
        Every security-relevant event: logins, user and API-key management, config changes,
        secret reveals, issue and job activity. Rows older than the retention window are
        pruned daily.
      </p>

      {audit.isError ? (
        <PageError message="Could not load the audit log" onRetry={() => audit.refetch()} />
      ) : audit.isLoading ? (
        <TableSkeleton columns={4} rows={8} />
      ) : audit.data ? (
        <Card>
          <CardContent className="p-0">
            <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
              <FilterSelect
                label="Actions"
                value={actionFilter}
                options={vocabulary.filterOptions}
                optionLabel={vocabulary.label}
                onChange={(v) => {
                  setActionFilter(v);
                  setOffset(0);
                }}
              />
              <div className="w-56">
                <Input
                  placeholder="Filter by actor…"
                  value={actorInput}
                  onChange={(e) => setActorInput(e.target.value)}
                  aria-label="Actor"
                />
              </div>
              <div className="w-40">
                <Select
                  aria-label="Window"
                  value={String(windowHours)}
                  onChange={(e) => {
                    setWindowHours(Number(e.target.value));
                    setOffset(0);
                  }}
                >
                  {WINDOW_OPTIONS.map((o) => (
                    <option key={o.hours} value={o.hours}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            {actions.isError && (
              <div role="status" className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2 text-xs text-warning">
                <span>
                  Could not load the action names. Each event is named from its identifier, and the Actions filter
                  lists no actions until they load.
                </span>
                <Button variant="link" size="xs" className="px-0" onClick={actions.refetch}>
                  Try again
                </Button>
              </div>
            )}

            {audit.data.entries.length === 0 ? (
              <EmptyState
                icon={ScrollText}
                title="No audit events"
                description="No rows match the current filters."
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Event</th>
                      <th className="hidden sm:table-cell">Actor</th>
                      <th className="hidden sm:table-cell">Issue</th>
                      <th className="text-right">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.data.entries.map((entry) => {
                      const detail = formatDetails(entry.action, entry.details);
                      const Icon = vocabulary.icon(entry.action);
                      return (
                        <tr key={entry.id}>
                          <td>
                            <div className="flex items-baseline gap-1.5">
                              <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0 self-center text-fg-muted" />
                              <span className="whitespace-nowrap font-medium text-white/80">
                                {vocabulary.label(entry.action)}
                              </span>
                              {detail && (
                                <span className="hidden truncate text-xs font-mono text-muted-foreground sm:inline">{detail}</span>
                              )}
                            </div>
                          </td>
                          <td className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">{entry.actor}</td>
                          <td className="hidden font-mono text-xs sm:table-cell">
                            {entry.issue_id ? (
                              <Link to={`/issues/${entry.issue_id}`} className="text-fg hover:text-primary hover:underline">
                                {entry.issue_id.slice(0, 12)}
                              </Link>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td className="whitespace-nowrap text-right text-xs text-muted-foreground">
                            {formatDateTime(entry.created_at)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <Pagination total={audit.data.total} offset={offset} limit={LIMIT} onPageChange={setOffset} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

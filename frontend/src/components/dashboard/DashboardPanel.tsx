import type { ReactNode } from "react";
import { Link } from "react-router";

interface DashboardPanelProps {
  title: string;
  viewAll?: { to: string; label: string };
  isPending: boolean;
  isError: boolean;
  isEmpty: boolean;
  emptyText: string;
  children: ReactNode;
}

export function DashboardPanel({
  title,
  viewAll,
  isPending,
  isError,
  isEmpty,
  emptyText,
  children,
}: DashboardPanelProps) {
  let body: ReactNode;
  if (isError) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">Unavailable</p>;
  } else if (isPending) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">Loading…</p>;
  } else if (isEmpty) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">{emptyText}</p>;
  } else {
    body = children;
  }

  return (
    <section
      data-testid={`panel-${title}`}
      className="overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <h2 className="text-section font-semibold">{title}</h2>
        {viewAll && (
          <Link
            to={viewAll.to}
            className="text-sm text-fg transition-colors hover:text-primary"
          >
            {viewAll.label} →
          </Link>
        )}
      </header>
      <div className="overflow-x-auto">{body}</div>
    </section>
  );
}

import { Card, CardContent } from "@/components/ui/Card";

interface TableSkeletonProps {
  columns?: number;
  rows?: number;
}

export function TableSkeleton({ columns, rows = 5 }: TableSkeletonProps) {
  if (columns === undefined) {
    // No column count provided — render a single spanning skeleton row per row.
    return (
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-10 w-full bg-muted/60 rounded animate-pulse" />
        ))}
      </div>
    );
  }
  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {Array.from({ length: columns }).map((_, i) => (
                  <th key={i} className="px-4 py-3">
                    <div className="h-3 w-20 bg-muted rounded animate-pulse" />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: rows }).map((_, rowIdx) => (
                <tr key={rowIdx} className="border-b border-border/50">
                  {Array.from({ length: columns }).map((_, colIdx) => (
                    <td key={colIdx} className="px-4 py-3">
                      <div
                        className="h-3 bg-muted/60 rounded animate-pulse"
                        style={{ width: `${40 + ((colIdx * 17 + rowIdx * 13) % 40)}%` }}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

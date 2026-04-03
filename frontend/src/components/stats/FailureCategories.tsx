import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

interface FailureCategoriesProps {
  categories: Record<string, number>;
}

export function FailureCategories({ categories }: FailureCategoriesProps) {
  const entries = Object.entries(categories).sort(([, a], [, b]) => b - a);
  const maxCount = entries.length > 0 ? entries[0][1] : 0;

  if (entries.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Failure Categories</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">No failures recorded.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Failure Categories</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {entries.map(([category, count]) => {
            const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
            return (
              <div key={category}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-foreground">{category}</span>
                  <span className="font-mono text-muted-foreground">{count}</span>
                </div>
                <div className="h-2.5 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full bg-destructive/80 transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

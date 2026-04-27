interface FailureCategoriesProps {
  categories: Record<string, number>;
}

export function FailureCategories({ categories }: FailureCategoriesProps) {
  const entries = Object.entries(categories).sort(([, a], [, b]) => b - a);
  const maxCount = entries.length > 0 ? entries[0][1] : 0;

  if (entries.length === 0) {
    return (
      <div className="athanor-card">
        <div className="px-6 pt-5 pb-2">
          <h2 className="text-lg font-semibold text-white">Failure Categories</h2>
        </div>
        <div className="px-6 pb-5">
          <p className="text-sm text-white/40">No failures recorded.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="athanor-card">
      <div className="px-6 pt-5 pb-2">
        <h2 className="text-lg font-semibold text-white">Failure Categories</h2>
      </div>
      <div className="px-6 pb-5">
        <div className="space-y-3">
          {entries.map(([category, count]) => {
            const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
            return (
              <div key={category}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className="text-white/70">{category}</span>
                  <span className="font-mono text-white/40">{count}</span>
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
      </div>
    </div>
  );
}

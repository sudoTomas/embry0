import type { QaCheckResult as CheckResult } from "@/api/qaConfig";

type CheckedApp = NonNullable<CheckResult["summary"]>["apps"][number];

/** One app in a sentence. An app embry0 does not start (a deployed target) has no start. */
function appLine(app: CheckedApp): string {
  const start = app.start ? ` starts with ${app.start},` : "";
  const where = app.address_is_local ? " on embry0's test computer" : "";
  const checks = `${app.criteria.length} ${app.criteria.length === 1 ? "check" : "checks"}`;
  return `${app.name}:${start} tested at ${app.address}${where}, ${checks}.`;
}

/** The result of the free test setup check, shown under the editor. */
export function QaCheckResult({ result }: { result: CheckResult }) {
  const { problems, warnings, summary } = result;
  return (
    <div className="space-y-3 text-sm" data-testid="qa-check-result">
      {problems.length > 0 && (
        <ul
          aria-label="Problems"
          className="list-disc space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 py-3 pl-7 pr-3 text-destructive"
          data-testid="qa-check-problems"
        >
          {problems.map((p, i) => (
            <li key={i}>{[p.plain, p.fix].filter(Boolean).join(" ")}</li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul
          aria-label="Warnings"
          className="list-disc space-y-1 rounded-lg border border-warning/20 bg-warning/[0.06] py-3 pl-7 pr-3 text-warning"
          data-testid="qa-check-warnings"
        >
          {warnings.map((w, i) => (
            <li key={i}>{w.plain}</li>
          ))}
        </ul>
      )}
      {result.ok && warnings.length === 0 && (
        <p className="text-success" data-testid="qa-check-ok">
          All good: every app has a work machine with a browser.
        </p>
      )}
      {summary && (
        <div className="space-y-1 text-white/70" data-testid="qa-check-summary">
          {summary.apps.map((app) => (
            <p key={app.name}>{appLine(app)}</p>
          ))}
          <p>When: {summary.when_plain}</p>
        </div>
      )}
    </div>
  );
}

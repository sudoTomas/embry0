import type { JobVerification } from "@/lib/types";

const VERDICT_CLASS: Record<string, string> = {
  passed: "text-palette-emerald border-palette-emerald/40",
  failed: "text-destructive border-destructive/40",
};

/** The Verification tab (spec 3.6): verdict, tier, the gate table from `report.gate.targets`, the browser QA line. */
export function VerificationTab({ verification }: { verification: JobVerification }) {
  const report = verification.report ?? {};
  const targets = report.gate?.targets ?? [];
  const reasons = report.human_merge_reasons ?? [];
  const verdict = verification.gate_verdict || report.gate?.verdict || "unknown";
  return (
    <div data-testid="verification-tab" className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span data-testid="verdict" data-verdict={verdict} className={`rounded border px-2 py-0.5 font-medium uppercase tracking-wide ${VERDICT_CLASS[verdict] ?? "text-muted-foreground border-border"}`}>
          {verdict}
        </span>
        <span className="text-muted-foreground">Tier</span>
        <span className="font-mono">{verification.tier}</span>
        {report.tier?.reason && <span className="text-muted-foreground">· {report.tier.reason}</span>}
        {verification.blocking && <span className="text-destructive">blocking</span>}
      </div>
      {targets.length === 0 ? (
        <p className="text-muted-foreground">No gate targets ran.</p>
      ) : (
        <table className="w-full text-left">
          <thead className="text-xs text-muted-foreground">
            <tr><th className="py-1 pr-3">Target</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Comparison</th><th className="py-1 pr-3">Failures</th><th className="py-1">Blocking</th></tr>
          </thead>
          <tbody>
            {targets.map((t) => (
              <tr key={t.target} className="border-t border-border">
                <td className="py-1 pr-3 font-mono">{t.target}</td>
                <td className="py-1 pr-3">{t.status}</td>
                <td className="py-1 pr-3">{t.comparison ?? "—"}</td>
                <td className="py-1 pr-3">
                  {(t.new_failures?.length ?? 0) > 0 && <span className="text-destructive">{t.new_failures!.length} new</span>}
                  {(t.new_failures?.length ?? 0) > 0 && (t.preexisting?.length ?? 0) > 0 && " · "}
                  {(t.preexisting?.length ?? 0) > 0 && <span className="text-muted-foreground">{t.preexisting!.length} pre-existing</span>}
                  {(t.new_failures?.length ?? 0) === 0 && (t.preexisting?.length ?? 0) === 0 && "—"}
                </td>
                <td className="py-1">{t.blocking ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        <span className="text-muted-foreground">Browser QA</span>{" "}
        {report.browser_qa?.planned ?? verification.browser_qa}
        {report.browser_qa?.verdict && ` · ${report.browser_qa.verdict}`}
        {report.browser_qa?.reason_code && <span className="text-muted-foreground"> · {report.browser_qa.reason_code}</span>}
      </p>
      {verification.human_merge_only && (
        <p className="text-palette-amber">
          Human merge only{reasons.length > 0 && <span className="text-muted-foreground"> · {reasons.join(", ")}</span>}
        </p>
      )}
      <p className="text-xs text-muted-foreground font-mono">
        {report.gate?.base_ref ?? "base"} {verification.base_sha.slice(0, 10)} → {verification.head_sha.slice(0, 10)}
      </p>
    </div>
  );
}

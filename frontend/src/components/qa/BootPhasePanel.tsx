/**
 * Phase 5A: dashboard drill-down for sub-tasks that failed at boot.
 *
 * Renders nothing when boot_phase is null/undefined (e.g. legacy runs and
 * the passed-boot path). Otherwise surfaces the BootResult fields the
 * orchestrator captured: outcome label, attempt count, elapsed duration,
 * the list of failed ready_checks, and a collapsible <details> with the
 * dev-server stdout tail (already truncated to 8 KiB at the backend).
 *
 * Wired into QaAppResultCard for status in {boot_failure, ready_check_failed}.
 * The component is forgiving on its own props — it accepts undefined, null,
 * or a fully populated BootPhaseDetail and renders sensibly in all three
 * cases so callers don't need to gate the render themselves.
 */
import type { BootPhaseDetail } from "@/lib/types";

interface Props {
  boot_phase: BootPhaseDetail | null | undefined;
}

function formatSeconds(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  // One decimal place for sub-minute durations, integer otherwise.
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
}

export function BootPhasePanel({ boot_phase }: Props) {
  if (!boot_phase) return null;
  const {
    outcome,
    attempts,
    duration_ms,
    failed_checks,
    boot_stdout_tail,
  } = boot_phase;
  return (
    <section
      data-testid="qa-boot-phase-panel"
      data-outcome={outcome}
      className="rounded-sm border border-white/10 bg-white/5 px-3 py-2 text-sm text-white/80"
    >
      <header className="font-medium text-white/90">
        Boot phase: {outcome}
        {" · "}
        {attempts} attempts
        {" · "}
        {formatSeconds(duration_ms)}
      </header>
      {failed_checks.length > 0 && (
        <div className="mt-2">
          <div className="text-xs uppercase tracking-wide text-white/50">
            Failed checks
          </div>
          <ul className="mt-1 list-disc pl-5 text-white/70">
            {failed_checks.map((check, idx) => (
              <li key={idx} className="break-words">
                {check}
              </li>
            ))}
          </ul>
        </div>
      )}
      {boot_stdout_tail && boot_stdout_tail.trim().length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs uppercase tracking-wide text-white/50">
            Boot stdout (last {boot_stdout_tail.length} bytes)
          </summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-sm bg-black/40 px-2 py-1 text-xs text-white/70">
            {boot_stdout_tail}
          </pre>
        </details>
      )}
    </section>
  );
}

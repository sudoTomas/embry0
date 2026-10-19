import { useState, type JSX, type ReactNode } from "react";
import { CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import type { QAAcceptanceResult } from "@/api/qa-artifacts";
import { attemptArtifactPath, fetchArtifactBlob } from "@/api/qa-artifacts";

// Long enough for the new tab to load the blob; it keeps its own copy once loaded.
const BLOB_URL_TTL_MS = 60_000;

const STATUS_ICON: Record<QAAcceptanceResult["status"], ReactNode> = {
  passed: <CheckCircle2 className="w-4 h-4 text-success" />,
  failed: <XCircle className="w-4 h-4 text-destructive" />,
  inconclusive: <HelpCircle className="w-4 h-4 text-warning" />,
};

interface Props {
  jobId: string;
  attemptN: number;
  results: QAAcceptanceResult[];
}

/**
 * One evidence file. The bytes come through the orchestrator passthrough
 * with the authenticated client and open in a new tab as a blob: URL (#541),
 * so the browser never follows a redirect to the storage endpoint.
 */
function EvidenceLink({ jobId, attemptN, path }: { jobId: string; attemptN: number; path: string }): JSX.Element {
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");

  const open = async () => {
    setState("loading");
    try {
      const blob = await fetchArtifactBlob(attemptArtifactPath(jobId, attemptN, path));
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noreferrer";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_TTL_MS);
      setState("idle");
    } catch {
      setState("error");
    }
  };

  return (
    <button
      type="button"
      onClick={() => void open()}
      disabled={state === "loading"}
      data-testid="qa-evidence-link"
      data-path={path}
      title={state === "error" ? `${path}: failed to load` : path}
      className={
        state === "error"
          ? "text-xs px-2 py-0.5 rounded bg-white/5 border border-destructive/40 text-destructive"
          : "text-xs px-2 py-0.5 rounded bg-white/5 border border-white/10 text-white/70 hover:text-white"
      }
    >
      {path.split("/").pop()}
    </button>
  );
}

export function QAAcceptanceResults({ jobId, attemptN, results }: Props): JSX.Element {
  if (results.length === 0) {
    return <p className="text-sm text-white/50 italic">No acceptance criteria recorded.</p>;
  }
  return (
    <div className="space-y-3">
      {results.map((r) => (
        <div key={r.criterion} className="border border-white/10 rounded p-3">
          <div className="flex items-center gap-2">
            {STATUS_ICON[r.status]}
            <span className="font-medium text-sm">{r.criterion}</span>
            <span className="text-xs uppercase text-white/50 ml-auto">{r.status}</span>
          </div>
          {r.notes && <p className="text-xs text-white/60 mt-2">{r.notes}</p>}
          {r.evidence.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {r.evidence.map((e) => (
                <EvidenceLink key={e} jobId={jobId} attemptN={attemptN} path={e} />
              ))}
            </div>
          )}
          {r.console_errors.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-destructive cursor-pointer">
                {r.console_errors.length} console error(s)
              </summary>
              <ul className="text-xs mt-1 space-y-1 font-mono">
                {r.console_errors.map((e, i) => (
                  <li key={i} className="text-destructive/80">{e}</li>
                ))}
              </ul>
            </details>
          )}
          {r.network_failures.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-warning cursor-pointer">
                {r.network_failures.length} network failure(s)
              </summary>
              <ul className="text-xs mt-1 space-y-1 font-mono">
                {r.network_failures.map((nf, i) => (
                  <li key={i} className="text-warning/80">[{nf.status}] {nf.url}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

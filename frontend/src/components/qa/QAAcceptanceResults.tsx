import type { JSX, ReactNode } from "react";
import { CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import type { QAAcceptanceResult } from "@/api/qa-artifacts";
import { artifactUrl } from "@/api/qa-artifacts";

const STATUS_ICON: Record<QAAcceptanceResult["status"], ReactNode> = {
  passed: <CheckCircle2 className="w-4 h-4 text-green-400" />,
  failed: <XCircle className="w-4 h-4 text-red-400" />,
  inconclusive: <HelpCircle className="w-4 h-4 text-yellow-400" />,
};

interface Props {
  jobId: string;
  results: QAAcceptanceResult[];
}

export function QAAcceptanceResults({ jobId, results }: Props): JSX.Element {
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
                <a
                  key={e}
                  href={artifactUrl(jobId, e)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs px-2 py-0.5 rounded bg-white/5 border border-white/10 text-white/70 hover:text-white"
                >
                  {e.split("/").pop()}
                </a>
              ))}
            </div>
          )}
          {r.console_errors.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-red-300 cursor-pointer">
                {r.console_errors.length} console error(s)
              </summary>
              <ul className="text-xs mt-1 space-y-1 font-mono">
                {r.console_errors.map((e, i) => (
                  <li key={i} className="text-red-200/80">{e}</li>
                ))}
              </ul>
            </details>
          )}
          {r.network_failures.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-orange-300 cursor-pointer">
                {r.network_failures.length} network failure(s)
              </summary>
              <ul className="text-xs mt-1 space-y-1 font-mono">
                {r.network_failures.map((nf, i) => (
                  <li key={i} className="text-orange-200/80">[{nf.status}] {nf.url}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

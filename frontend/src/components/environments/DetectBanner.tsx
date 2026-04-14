import { useState } from "react";
import { useDetectRepoEnv } from "@/hooks/useEnvironments";
import { AlertTriangle, Download, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { DetectedEnvVar } from "@/lib/types/environment";

interface DetectBannerProps {
  owner: string;
  repo: string;
  onImport: (variables: DetectedEnvVar[]) => void;
}

export function DetectBanner({ owner, repo, onImport }: DetectBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  const { data, isLoading } = useDetectRepoEnv(owner, repo);

  if (dismissed || isLoading || !data || data.unconfigured_count === 0) {
    return null;
  }

  const unconfigured = data.variables.filter((v) => !v.is_configured);

  return (
    <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="h-5 w-5 text-amber-400 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-200">
            {data.unconfigured_count} unconfigured variable{data.unconfigured_count !== 1 ? "s" : ""} detected
          </p>
          <p className="text-xs text-amber-200/60 mt-1">
            Found in <span className="font-mono">{data.source_file}</span> but not yet configured for this repository.
          </p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {unconfigured.map((v) => (
              <span
                key={v.key}
                className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-mono bg-amber-500/10 text-amber-300"
              >
                {v.key}
              </span>
            ))}
          </div>
          <div className="flex items-center gap-2 mt-3">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs border-amber-500/30 text-amber-300 hover:bg-amber-500/10 hover:text-amber-200"
              onClick={() => onImport(unconfigured)}
            >
              <Download className="h-3 w-3 mr-1" />
              Import All
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs text-amber-300/60 hover:text-amber-300"
              onClick={() => setDismissed(true)}
            >
              Dismiss
            </Button>
          </div>
        </div>
        <button
          onClick={() => setDismissed(true)}
          className="text-amber-300/40 hover:text-amber-300 transition-colors cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

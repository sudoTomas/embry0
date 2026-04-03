import { CheckCircle2, ExternalLink, Clock, DollarSign, Users } from "lucide-react";

interface ResultsSummaryProps {
  prUrl?: string | null;
  totalCost: number;
  startedAt?: string | null;
  finishedAt?: string | null;
  agentsRun: number;
}

export function ResultsSummary({ prUrl, totalCost, startedAt, finishedAt, agentsRun }: ResultsSummaryProps) {
  const duration = startedAt && finishedAt
    ? Math.floor((new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000)
    : 0;
  const durationStr = duration >= 60
    ? `${Math.floor(duration / 60)}m ${duration % 60}s`
    : `${duration}s`;

  return (
    <div className="legion-card p-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-[10px] flex items-center justify-center bg-emerald-500/12 border border-emerald-500/25">
          <CheckCircle2 className="w-5 h-5 text-emerald-500" />
        </div>
        <div>
          <h3 className="text-lg font-bold text-emerald-400">Job Completed</h3>
          {prUrl && (
            <a
              href={prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
            >
              {prUrl.replace("https://github.com/", "")}
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>
      </div>

      <div className="flex items-center gap-6 text-sm text-white/50">
        <div className="flex items-center gap-1.5">
          <DollarSign className="w-3.5 h-3.5" />
          <span className="font-mono">${totalCost.toFixed(3)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          <span>{durationStr}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Users className="w-3.5 h-3.5" />
          <span>{agentsRun} agents</span>
        </div>
      </div>
    </div>
  );
}

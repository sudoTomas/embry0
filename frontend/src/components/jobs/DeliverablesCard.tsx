import { useState } from "react";
import { Download, ExternalLink, FileText, GitPullRequest, MessageSquare, Package } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { useDeliverables } from "@/hooks/useDeliverables";
import { downloadDeliverable, type Deliverable } from "@/api/deliverables";

function formatBytes(n: number | null): string {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const REPORT_COLLAPSE_CHARS = 800;

function ReportBody({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const needsToggle = content.length > REPORT_COLLAPSE_CHARS;
  const shown = expanded || !needsToggle ? content : content.slice(0, REPORT_COLLAPSE_CHARS) + "…";
  return (
    <div>
      <pre className="whitespace-pre-wrap font-sans text-xs text-white/70 leading-relaxed">{shown}</pre>
      {needsToggle && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="mt-1 text-[11px] text-cyan-400 hover:underline"
        >
          {expanded ? "Show less" : "Show full report"}
        </button>
      )}
    </div>
  );
}

function DeliverableRow({ deliverable }: { deliverable: Deliverable }) {
  const handleDownload = async () => {
    try {
      await downloadDeliverable(deliverable);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to download artifact");
    }
  };

  if (deliverable.type === "pr" && deliverable.url) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <GitPullRequest className="w-3.5 h-3.5 text-violet-400 shrink-0" />
        <a
          href={deliverable.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-violet-400 hover:underline truncate"
        >
          {deliverable.url}
        </a>
        <ExternalLink className="w-3 h-3 text-white/30 shrink-0" />
      </div>
    );
  }

  if (deliverable.type === "artifact") {
    return (
      <div className="flex items-center gap-2 text-xs">
        <Package className="w-3.5 h-3.5 text-amber-400 shrink-0" />
        <span className="font-mono truncate text-white/70">{deliverable.title}</span>
        <span className="text-white/30 shrink-0">{formatBytes(deliverable.size_bytes)}</span>
        <Button variant="ghost" size="sm" className="h-6 gap-1 text-[11px] ml-auto shrink-0" onClick={handleDownload}>
          <Download className="w-3 h-3" /> Download
        </Button>
      </div>
    );
  }

  // report / message — inline text content.
  const Icon = deliverable.type === "message" ? MessageSquare : FileText;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-xs text-white/50">
        <Icon className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
        <span className="uppercase tracking-wide text-[10px]">{deliverable.title || deliverable.type}</span>
      </div>
      {deliverable.content && <ReportBody content={deliverable.content} />}
    </div>
  );
}

export function DeliverablesCard({ jobId }: { jobId: string }) {
  const { data: deliverables } = useDeliverables(jobId);
  if (!deliverables || deliverables.length === 0) return null;

  return (
    <div className="px-4 py-3 rounded-lg border border-white/[0.08] bg-white/[0.02] space-y-3">
      <div className="text-xs font-medium text-white/60 uppercase tracking-wide">
        Deliverables ({deliverables.length})
      </div>
      {deliverables.map((d) => (
        <DeliverableRow key={d.id} deliverable={d} />
      ))}
    </div>
  );
}

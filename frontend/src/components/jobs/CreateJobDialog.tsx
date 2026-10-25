import { useState } from "react";
import { type RunWith, runWithBody } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { useCreateJob } from "@/hooks/useJobs";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Dialog } from "@/components/ui/Dialog";
import { Switch } from "@/components/ui/Switch";
import {
  EMPTY_ADVANCED,
  JobAdvancedFields,
  advancedToRequest,
  type JobAdvancedState,
} from "@/components/jobs/JobAdvancedFields";
import { toast } from "sonner";
import type { JobContextType, JobCreateRequest } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";

interface CreateJobDialogProps {
  onClose: () => void;
}

const CONTEXT_HELP: Record<JobContextType, string> = {
  git: "Clone a GitHub repository into the sandbox (the issue-to-PR flow).",
  http: "Fetch a single http(s) source into the workspace — research/analysis jobs.",
  local: "Mount an allowlisted absolute host path into the workspace.",
  none: "Empty workspace — pure research/ops jobs that need no inputs.",
};

export function CreateJobDialog({ onClose }: CreateJobDialogProps) {
  const [contextType, setContextType] = useState<JobContextType>("git");
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [task, setTask] = useState("");
  const [issueNumber, setIssueNumber] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advanced, setAdvanced] = useState<JobAdvancedState>(EMPTY_ADVANCED);
  const [runWith, setRunWith] = useState<RunWith>({});
  const createJob = useCreateJob();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (contextType === "git" && !/^[\w.-]+\/[\w.-]+$/.test(repo)) {
      toast.error("Repository must be in owner/name format");
      return;
    }
    if (contextType === "http" && !/^https?:\/\//.test(url)) {
      toast.error("Source URL must start with http:// or https://");
      return;
    }
    if (contextType === "local" && !path.startsWith("/")) {
      toast.error("Local path must be absolute");
      return;
    }

    const req: JobCreateRequest = {
      task,
      context: {
        type: contextType,
        ...(contextType === "git" ? { repo, ...(ref ? { ref } : {}) } : {}),
        ...(contextType === "http" ? { url } : {}),
        ...(contextType === "local" ? { path } : {}),
      },
      ...(contextType === "git" && issueNumber ? { issue_number: Number(issueNumber) } : {}),
      ...advancedToRequest(advanced),
      ...runWithBody(runWith),
    };

    createJob.mutate(req, {
      onSuccess: () => {
        toast.success("Job created");
        onClose();
      },
      onError: (err) => toastApiError(err, "Failed"),
    });
  };

  return (
    <Dialog onClose={onClose} title="Create New Job" dismissOnBackdrop={false} className="max-w-2xl">
      <div className="p-5">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="context-type">Context</Label>
              <Select
                id="context-type"
                data-autofocus
                value={contextType}
                onChange={(e) => setContextType(e.target.value as JobContextType)}
              >
                <option value="git">Git repository</option>
                <option value="http">HTTP source</option>
                <option value="local">Local path</option>
                <option value="none">None (empty workspace)</option>
              </Select>
              <p className="text-xs text-white/30 mt-1">{CONTEXT_HELP[contextType]}</p>
            </div>
            {contextType === "git" && (
              <div>
                <Label htmlFor="issue">Issue Number (optional)</Label>
                <Input
                  id="issue"
                  type="number"
                  value={issueNumber}
                  onChange={(e) => setIssueNumber(e.target.value)}
                  placeholder="123"
                />
              </div>
            )}
          </div>

          {contextType === "git" && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="repo">Repository</Label>
                <Input
                  id="repo"
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  placeholder="owner/repo"
                  required
                />
              </div>
              <div>
                <Label htmlFor="ref">Branch / Ref (optional)</Label>
                <Input
                  id="ref"
                  value={ref}
                  onChange={(e) => setRef(e.target.value)}
                  placeholder="main"
                />
              </div>
            </div>
          )}
          {contextType === "http" && (
            <div>
              <Label htmlFor="source-url">Source URL</Label>
              <Input
                id="source-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/data.csv"
                required
              />
            </div>
          )}
          {contextType === "local" && (
            <div>
              <Label htmlFor="local-path">Host Path</Label>
              <Input
                id="local-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/data/inputs"
                required
              />
              <p className="text-xs text-white/30 mt-1">
                Must be absolute and inside the orchestrator's local-context allowlist.
              </p>
            </div>
          )}

          <div>
            <Label htmlFor="task">Task</Label>
            <Textarea
              id="task"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Describe what needs to be done..."
              className="min-h-[100px]"
              required
            />
          </div>

          <RunWithControl value={runWith} onChange={setRunWith} repo={contextType === "git" ? repo || null : null} />

          <div className="flex items-center gap-3">
            <Switch id="show-advanced" checked={showAdvanced} onCheckedChange={setShowAdvanced} />
            <Label htmlFor="show-advanced">Advanced options</Label>
          </div>
          {showAdvanced && <JobAdvancedFields value={advanced} onChange={setAdvanced} />}

          <div className="flex gap-2 justify-end">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={createJob.isPending}>
              {createJob.isPending ? "Creating..." : "Create Job"}
            </Button>
          </div>
        </form>
      </div>
    </Dialog>
  );
}

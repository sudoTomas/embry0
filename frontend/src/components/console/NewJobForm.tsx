import { useMemo, useState } from "react";
import { type RunWith, runWithBody } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useCreateJob } from "@/hooks/useJobs";
import { useGitHubRepos } from "@/hooks/useGitHub";
import {
  EMPTY_ADVANCED,
  JobAdvancedFields,
  advancedToRequest,
  type JobAdvancedState,
} from "@/components/jobs/JobAdvancedFields";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { toastApiError } from "@/api/errorToast";

interface NewJobFormProps {
  onClose: () => void;
  /** Repos already visible on the board — merged into the GitHub-listed set
   * (the CreateIssueDialog merge pattern) so in-flight repos are always
   * pickable even when the GitHub listing is down. */
  knownRepos?: string[];
}

/**
 * Launch form for the Console board: repo picker + task textarea, context
 * fixed to `git` (a bare repo+task POST /jobs IS the git context; non-git
 * contexts launch from the Jobs page dialog). Increment 3 adds the shared
 * Advanced section (template / profile / budget / per-agent models). On
 * success it auto-navigates to the new job's detail page — the paperclip
 * launch→observe pattern.
 */
export function NewJobForm({ onClose, knownRepos = [] }: NewJobFormProps) {
  const [repo, setRepo] = useState("");
  const [task, setTask] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advanced, setAdvanced] = useState<JobAdvancedState>(EMPTY_ADVANCED);
  const [runWith, setRunWith] = useState<RunWith>({});
  const createJob = useCreateJob();
  const navigate = useNavigate();

  // Pull every repo the GITHUB_TOKEN can see; merge with repos already on
  // the board so the picker never goes empty while jobs are in flight.
  const { data: gh, isLoading: ghLoading, isError: ghError } = useGitHubRepos();
  const repoOptions = useMemo(() => {
    const fromGitHub = gh?.repos.map((r) => r.full_name) ?? [];
    const merged = new Set<string>([...fromGitHub, ...knownRepos]);
    return Array.from(merged).sort();
  }, [gh, knownRepos]);
  const repoHelpText = ghError
    ? "Could not load repos from GitHub — check GITHUB_TOKEN. Falls back to repos already on the board."
    : ghLoading
      ? "Loading repos from GitHub…"
      : null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createJob.mutate(
      { repo, task, ...advancedToRequest(advanced), ...runWithBody(runWith) },
      {
        onSuccess: (job) => {
          toast.success("Job dispatched");
          navigate(`/jobs/${job.job_id}`);
        },
        onError: (err) => toastApiError(err, "Failed"),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">New job</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <Label htmlFor="console-new-job-repo">Repository</Label>
            <Select
              id="console-new-job-repo"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              required
            >
              <option value="">Select repository…</option>
              {repoOptions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </Select>
            {repoHelpText && <p className="mt-1 text-xs text-white/30">{repoHelpText}</p>}
          </div>
          <div>
            <Label htmlFor="console-new-job-task">Task</Label>
            <Textarea
              id="console-new-job-task"
              value={task}
              onChange={(e) => setTask(e.target.value)}
              placeholder="Describe what needs to be done..."
              className="min-h-[100px]"
              required
            />
          </div>
          <RunWithControl value={runWith} onChange={setRunWith} repo={repo || null} />
          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="console-new-job-advanced"
              checked={showAdvanced}
              onChange={(e) => setShowAdvanced(e.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            <Label htmlFor="console-new-job-advanced">Advanced options</Label>
          </div>
          {showAdvanced && (
            <JobAdvancedFields value={advanced} onChange={setAdvanced} idPrefix="console-job-adv" />
          )}

          <div className="flex gap-2 justify-end">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={createJob.isPending || !repo || !task.trim()}>
              {createJob.isPending ? "Dispatching..." : "Dispatch"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

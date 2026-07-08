import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useCreateJob } from "@/hooks/useJobs";
import { useGitHubRepos } from "@/hooks/useGitHub";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";

interface NewJobFormProps {
  onClose: () => void;
  /** Repos already visible on the board — merged into the GitHub-listed set
   * (the CreateIssueDialog merge pattern) so in-flight repos are always
   * pickable even when the GitHub listing is down. */
  knownRepos?: string[];
}

/**
 * Minimal launch form for the Console board (spec Increment 1): repo picker
 * + task textarea, defaults only — context is fixed to `git` (a bare
 * repo+task POST /jobs IS the git context; no pipeline/profile options in
 * this increment). On success it auto-navigates to the new job's detail page
 * — the paperclip launch→observe pattern.
 */
export function NewJobForm({ onClose, knownRepos = [] }: NewJobFormProps) {
  const [repo, setRepo] = useState("");
  const [task, setTask] = useState("");
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
      { repo, task },
      {
        onSuccess: (job) => {
          toast.success("Job dispatched");
          navigate(`/jobs/${job.job_id}`);
        },
        onError: (err) => toast.error(`Failed: ${err.message}`),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">New Job</CardTitle>
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

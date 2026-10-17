import { useState } from "react";
import { useNavigate } from "react-router";
import { isAxiosError } from "axios";
import { Bot } from "lucide-react";
import { useCreateJob } from "@/hooks/useJobs";
import { useRegisteredRepos } from "@/hooks/useRepos";
import type { RegisteredRepo } from "@/api/repos";
import { formatErrorDetail } from "@/api/errorDetail";
import type { JobCreateRequest } from "@/lib/types";
import { RepoPicker } from "@/components/RepoPicker";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";

const FORGE_LABEL: Record<RegisteredRepo["forge"], string> = { github: "GitHub", gitlab: "GitLab" };

function forgeAnnotation(r: RegisteredRepo): string {
  return `${r.repo} · ${FORGE_LABEL[r.forge] ?? r.forge}`;
}

/**
 * #29: start the onboarding agent (the `onboard` pipeline) from the dashboard.
 * The same POST /jobs request `embry0 onboard` sends; on success the job page
 * streams its analyze, validate and smoke rounds.
 */
export function OnboardAgentForm({ initialRepo = "" }: { initialRepo?: string }) {
  const navigate = useNavigate();
  const createJob = useCreateJob();
  const { data: repos = [] } = useRegisteredRepos();

  const [repo, setRepo] = useState(initialRepo);
  const [branch, setBranch] = useState("");
  const [skipSmoke, setSkipSmoke] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pending = createJob.isPending;
  const hasStoredConfig = repos.some((r) => r.repo === repo && r.has_qa_config);

  function submit() {
    setError(null);
    const req: JobCreateRequest = { pipeline: "onboard", repo, skip_smoke: skipSmoke };
    // Left out, the orchestrator analyzes the repo's base branch.
    if (branch.trim()) req.branch = branch.trim();
    createJob.mutate(req, {
      onSuccess: (job) => void navigate(`/jobs/${job.job_id}`),
      onError: (err) => {
        const detail = isAxiosError(err) ? formatErrorDetail(err.response?.data?.detail) : null;
        setError(detail ?? (err instanceof Error ? err.message : "Could not start the onboarding agent"));
      },
    });
  }

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Card className="p-4 space-y-3">
        <div>
          <h3 className="text-sm font-semibold">Let the agent work it out</h3>
          <p className="text-xs text-muted-foreground mt-1">
            The agent clones the repo, drafts its qa.yaml (v2), validates it and smoke-boots each app,
            retrying on failures, then stores the config. Any registered repo, GitHub or GitLab.
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="onboard-agent-repo">Repository</Label>
          <RepoPicker
            id="onboard-agent-repo"
            value={repo}
            onChange={setRepo}
            annotate={forgeAnnotation}
            disabled={pending}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="onboard-agent-branch">Branch</Label>
          <Input
            id="onboard-agent-branch"
            className="font-mono"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="the repo's base branch"
            disabled={pending}
          />
          <p className="text-xs text-muted-foreground">
            Leave blank for the repo's base branch: its preference, else its forge's default branch.
          </p>
        </div>
        <div className="space-y-1">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={skipSmoke}
              onChange={(e) => setSkipSmoke(e.target.checked)}
              disabled={pending}
              aria-describedby="onboard-agent-skip-smoke-help"
            />
            Skip the smoke boot
          </label>
          <p id="onboard-agent-skip-smoke-help" className="text-xs text-muted-foreground">
            Store the config once it passes schema validation, without booting the apps.
          </p>
        </div>
      </Card>

      {hasStoredConfig && (
        <p className="text-sm text-amber-200" data-testid="onboard-agent-replaces-config">
          {repo} already has a stored QA config. A config the agent validates replaces it.
        </p>
      )}
      {error && (
        <p className="text-sm text-destructive" data-testid="onboard-agent-error">
          {error}
        </p>
      )}

      <Button type="submit" disabled={pending || !repo} data-testid="onboard-agent-submit">
        <Bot className="h-4 w-4" />
        {pending ? "Starting…" : "Start the onboarding agent"}
      </Button>
    </form>
  );
}

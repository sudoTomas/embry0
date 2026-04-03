import { useState } from "react";
import { useCreateJob } from "@/hooks/useJobs";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PipelineEditor } from "@/components/pipeline-editor/PipelineEditor";
import { toast } from "sonner";
import { Workflow } from "lucide-react";
import type { PipelineGraph, ProviderMode } from "@/lib/types";

interface CreateJobDialogProps {
  onClose: () => void;
}

export function CreateJobDialog({ onClose }: CreateJobDialogProps) {
  const [repo, setRepo] = useState("");
  const [task, setTask] = useState("");
  const [issueNumber, setIssueNumber] = useState("");
  const [maxBudget, setMaxBudget] = useState("10");
  const [providerMode, setProviderMode] = useState<ProviderMode>("anthropic_api");
  const [configurePipeline, setConfigurePipeline] = useState(false);
  const [showPipelineEditor, setShowPipelineEditor] = useState(false);
  const [pipelineGraph, setPipelineGraph] = useState<PipelineGraph | null>(null);
  const createJob = useCreateJob();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createJob.mutate(
      {
        repo,
        task,
        issue_number: issueNumber ? Number(issueNumber) : null,
        max_budget_usd: maxBudget ? Number(maxBudget) : null,
        provider_mode: providerMode,
        pipeline_graph: configurePipeline && pipelineGraph
          ? (pipelineGraph as unknown as Record<string, unknown>)
          : null,
      },
      {
        onSuccess: () => {
          toast.success("Job created");
          onClose();
        },
        onError: (e) => toast.error(`Failed: ${e.message}`),
      }
    );
  };

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Create New Job</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
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
                <Label htmlFor="issue">Issue Number (optional)</Label>
                <Input
                  id="issue"
                  type="number"
                  value={issueNumber}
                  onChange={(e) => setIssueNumber(e.target.value)}
                  placeholder="123"
                />
              </div>
            </div>
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
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="budget">Max Budget (USD)</Label>
                <Input
                  id="budget"
                  type="number"
                  step="0.01"
                  value={maxBudget}
                  onChange={(e) => setMaxBudget(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="provider">Provider Mode</Label>
                <Select
                  id="provider"
                  value={providerMode}
                  onChange={(e) => setProviderMode(e.target.value as ProviderMode)}
                >
                  <option value="anthropic_api">Anthropic API</option>
                  <option value="claude_max">Claude Max</option>
                  <option value="ollama">Ollama</option>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                id="configure-pipeline"
                checked={configurePipeline}
                onChange={(e) => setConfigurePipeline(e.target.checked)}
                className="h-4 w-4 rounded border-input"
              />
              <Label htmlFor="configure-pipeline">
                Configure Pipeline Manually
              </Label>
            </div>
            {configurePipeline && (
              <div className="flex items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setShowPipelineEditor(true)}
                  className="flex items-center gap-2 text-sm bg-white/[0.04] hover:bg-white/[0.08] border border-white/10 rounded-lg px-4 py-2 text-white/70 hover:text-white transition-colors"
                >
                  <Workflow size={16} />
                  {pipelineGraph
                    ? `Pipeline: ${pipelineGraph.nodes.length} agents, ${pipelineGraph.edges.length} edges`
                    : "Open Pipeline Editor"}
                </button>
                {pipelineGraph && (
                  <button
                    type="button"
                    onClick={() => setPipelineGraph(null)}
                    className="text-xs text-white/30 hover:text-red-400"
                  >
                    Clear
                  </button>
                )}
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={createJob.isPending}>
                {createJob.isPending ? "Creating..." : "Create Job"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {showPipelineEditor && (
        <PipelineEditor
          initialGraph={pipelineGraph ?? undefined}
          onApply={(graph) => {
            setPipelineGraph(graph);
            setShowPipelineEditor(false);
            toast.success("Pipeline applied");
          }}
          onClose={() => setShowPipelineEditor(false)}
        />
      )}
    </>
  );
}

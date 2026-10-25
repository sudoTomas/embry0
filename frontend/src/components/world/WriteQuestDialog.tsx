import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useWriteQuest } from "@/hooks/useWorld";

interface WriteQuestDialogProps {
  characters: readonly WorldCharacter[];
  repos: readonly string[];
  initialAgent: string | null;
  onClose: () => void;
  onSent: (jobId: string) => void;
}

/** A task the operator writes for one agent on one repository, with Run with. */
export function WriteQuestDialog({ characters, repos, initialAgent, onClose, onSent }: WriteQuestDialogProps) {
  const lookup = useIdentityLookup();
  const able = characters.filter((c) => c.can_quest);
  const [agent, setAgent] = useState(initialAgent && able.some((c) => c.agent_type === initialAgent) ? initialAgent : (able[0]?.agent_type ?? ""));
  const [repo, setRepo] = useState(repos[0] ?? "");
  const [task, setTask] = useState("");
  const [runWith, setRunWith] = useState<RunWith>({});
  const write = useWriteQuest();
  const character = able.find((c) => c.agent_type === agent);
  const identity = lookup(agent);
  const go = () =>
    write.mutate(
      { repo, task: task.trim(), template: character?.quest_template ?? null, runWith },
      {
        onSuccess: (job) => {
          toast.success(`Started ${job.job_id}`);
          onSent(job.job_id);
        },
        onError: (e) => toastApiError(e, "Not started"),
      },
    );
  return (
    <Dialog onClose={onClose} title="New task" className="max-w-lg">
      <div className="space-y-4 p-5" data-testid="write-quest">
        <div className="space-y-1.5">
          <Label htmlFor="new-task-agent">Agent</Label>
          <div className="flex items-center gap-2">
            {agent && <AgentAvatar identity={identity} size={32} state="idle" />}
            <Select id="new-task-agent" value={agent} onChange={(e) => setAgent(e.target.value)}>
              {able.map((c) => (
                <option key={c.agent_type} value={c.agent_type}>
                  {lookup(c.agent_type).displayName} (level {c.level})
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-task-repo">Repository</Label>
          <Select id="new-task-repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
            {repos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="new-task-text">Task</Label>
          <Textarea
            id="new-task-text"
            rows={5}
            placeholder="What should it do, and how will you know it is done?"
            value={task}
            onChange={(e) => setTask(e.target.value)}
          />
        </div>
        <RunWithControl value={runWith} onChange={setRunWith} repo={repo || null} />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!agent || !repo || !task.trim() || write.isPending} onClick={go}>
            {write.isPending ? "Starting…" : "Start"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

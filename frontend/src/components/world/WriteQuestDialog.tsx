import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useWriteQuest } from "@/hooks/useWorld";

interface WriteQuestDialogProps {
  characters: readonly WorldCharacter[];
  repos: readonly string[];
  initialAgent: string | null;
  onClose: () => void;
  onSent: (jobId: string) => void;
}

/** A quest the player writes: a task, for one character, on one repository, with Run with. */
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
          toast.success(`${identity.displayName} sets off.`);
          onSent(job.job_id);
        },
        onError: (e) => toastApiError(e, "The quest was not given"),
      },
    );
  return (
    <Dialog onClose={onClose} label="Write a quest" className="world max-w-lg">
      <div className="world-panel space-y-3 p-4" data-testid="write-quest">
        <h2 className="world-title text-base">Write a quest</h2>
        <label className="block space-y-1 text-xs">
          <span className="world-muted">Who goes</span>
          <div className="flex items-center gap-2">
            {agent && <AgentAvatar identity={identity} size={32} state="idle" />}
            <select aria-label="Character" className="world-slot h-8 flex-1 px-1 world-text" value={agent} onChange={(e) => setAgent(e.target.value)}>
              {able.map((c) => (
                <option key={c.agent_type} value={c.agent_type}>
                  {lookup(c.agent_type).displayName} (level {c.level})
                </option>
              ))}
            </select>
          </div>
        </label>
        <label className="block space-y-1 text-xs">
          <span className="world-muted">Where</span>
          <select aria-label="Repository" className="world-slot h-8 w-full px-1 world-text" value={repo} onChange={(e) => setRepo(e.target.value)}>
            {repos.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
        <label className="block space-y-1 text-xs">
          <span className="world-muted">The quest</span>
          <textarea
            aria-label="Task"
            className="world-slot w-full p-2 text-sm world-text"
            rows={5}
            placeholder="What should it do, and how will you know it is done?"
            value={task}
            onChange={(e) => setTask(e.target.value)}
          />
        </label>
        <div className="world-text text-xs">
          <RunWithControl value={runWith} onChange={setRunWith} repo={repo || null} />
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" className="world-button" onClick={onClose}>
            Not now
          </button>
          <button type="button" className="world-button" data-tone="win" disabled={!agent || !repo || !task.trim() || write.isPending} onClick={go}>
            {write.isPending ? "Setting off…" : "Send"}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

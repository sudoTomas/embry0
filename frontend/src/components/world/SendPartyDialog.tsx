import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { Lead, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useSendParty } from "@/hooks/useWorld";
import { KIND_WORDS, placeOf } from "@/lib/world";
import { CreatureIcon } from "./CreatureIcon";

interface SendPartyDialogProps {
  lead: Lead;
  characters: readonly WorldCharacter[];
  /** Who the answer is recorded as: the signed-in player. */
  by: string;
  canSend: boolean;
  onClose: () => void;
  onSent: (jobId: string | null) => void;
}

/**
 * A quest from the board: what it is, where, the evidence, what sending a
 * party starts, who leads it, and the model and key it runs with. The verb
 * button is the owner proposal's yes.
 */
export function SendPartyDialog({ lead, characters, by, canSend, onClose, onSent }: SendPartyDialogProps) {
  const lookup = useIdentityLookup();
  const [runWith, setRunWith] = useState<RunWith>({});
  const send = useSendParty();
  const leader = characters.find((c) => c.agent_type === lead.leader);
  const identity = lookup(lead.leader);
  const go = () =>
    send.mutate(
      { leadId: lead.id, by, runWith },
      {
        onSuccess: (done) => {
          toast.success(`${identity.displayName} sets off${done.job_id ? ` (${done.job_id})` : ""}.`);
          onSent(done.job_id);
        },
        onError: (e) => toastApiError(e, "The party did not set off"),
      },
    );

  return (
    <Dialog onClose={onClose} label={`Quest: ${lead.title}`} className="world max-w-xl">
      <div className="world-panel space-y-3 p-4" data-testid="send-party">
        <div className="flex items-start gap-3">
          <CreatureIcon creature={lead.creature} size={48} label={KIND_WORDS[lead.kind] ?? lead.kind} />
          <div className="min-w-0 flex-1">
            <h2 className="world-title text-base">{lead.title}</h2>
            <p className="world-muted text-xs">
              Level {lead.level} · {placeOf(lead.repo, lead.area)} · {KIND_WORDS[lead.kind] ?? lead.kind}
            </p>
          </div>
        </div>
        <p className="world-text text-sm">{lead.summary}</p>
        {lead.details && (
          <details className="world-slot rounded-sm p-2 text-xs world-text">
            <summary className="world-subtitle cursor-pointer">Examine</summary>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[11px]">{lead.details}</pre>
          </details>
        )}
        <p className="world-text text-xs">
          <span className="world-subtitle">If you send them: </span>
          {lead.action}.
        </p>
        <div className="world-slot flex items-center gap-2 rounded-sm p-2">
          <AgentAvatar identity={identity} size={32} state="idle" />
          <span className="world-text text-sm">
            {identity.displayName} leads{leader ? ` (level ${leader.level})` : ""}
          </span>
          {leader && leader.state !== "idle" && <span className="world-muted text-xs">· already out on another quest</span>}
        </div>
        <div className="world-text text-xs">
          <RunWithControl value={runWith} onChange={setRunWith} repo={lead.repo} />
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {lead.url && (
            <a className="world-muted text-xs underline" href={lead.url} target="_blank" rel="noreferrer">
              Open the ticket
            </a>
          )}
          <button type="button" className="world-button" onClick={onClose}>
            Not now
          </button>
          <button type="button" className="world-button" data-tone="win" disabled={!canSend || send.isPending} onClick={go}>
            {send.isPending ? "Setting off…" : lead.verb}
          </button>
        </div>
        {!canSend && <p className="world-muted text-xs">Only an admin can send a party.</p>}
      </div>
    </Dialog>
  );
}

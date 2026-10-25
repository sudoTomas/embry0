import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { Lead, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useSendParty } from "@/hooks/useWorld";
import { BAND_TONE, KIND_WORD, levelBand, placeOf } from "@/lib/world";
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
 * A lead from the board: what it is, where, the evidence, what starting it
 * runs, which agent leads, and the model and key it runs with. Start is the
 * owner proposal's yes.
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
          toast.success(`Started${done.job_id ? ` ${done.job_id}` : ""}`);
          onSent(done.job_id);
        },
        onError: (e) => toastApiError(e, "Not started"),
      },
    );

  return (
    <Dialog onClose={onClose} title={lead.title} className="max-w-xl">
      <div className="space-y-4 p-5" data-testid="send-party">
        <div className="flex items-start gap-3">
          <CreatureIcon kind={lead.kind} creature={lead.creature} size={48} label={KIND_WORD[lead.kind] ?? lead.kind} />
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            <Badge tone="neutral">{KIND_WORD[lead.kind] ?? lead.kind}</Badge>
            <Badge tone={BAND_TONE[levelBand(lead.level, leader?.level ?? lead.level)]} className="hud">
              Lvl {lead.level}
            </Badge>
            <span className="truncate font-mono text-xs text-fg-muted">{placeOf(lead.repo, lead.area)}</span>
          </div>
        </div>
        <p className="text-sm text-fg">{lead.summary}</p>
        {lead.details && (
          <details className="rounded-md border border-border p-2.5 text-xs">
            <summary className="cursor-pointer text-fg-secondary">Evidence</summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-2xs text-fg-secondary">{lead.details}</pre>
          </details>
        )}
        <p className="text-sm text-fg-secondary">
          <span className="font-semibold text-fg">Starting it: </span>
          {lead.action}.
        </p>
        <div className="flex items-center gap-2.5 rounded-md border border-border p-2.5">
          <AgentAvatar identity={identity} size={32} state="idle" />
          <span className="text-sm text-fg">
            {identity.displayName} leads{leader ? <span className="hud text-fg-secondary"> · Lvl {leader.level}</span> : null}
          </span>
          {leader && leader.state !== "idle" && <span className="text-xs text-fg-muted">· busy with another job</span>}
        </div>
        <RunWithControl value={runWith} onChange={setRunWith} repo={lead.repo} />
        <div className="flex flex-wrap items-center justify-end gap-2">
          {lead.url && (
            <a className="mr-auto text-xs text-fg-muted underline hover:text-fg" href={lead.url} target="_blank" rel="noreferrer">
              Open the source
            </a>
          )}
          <Button variant="ghost" onClick={onClose}>
            Not now
          </Button>
          <Button disabled={!canSend || send.isPending} onClick={go}>
            {send.isPending ? "Starting…" : "Start"}
          </Button>
        </div>
        {!canSend && <p className="text-xs text-fg-muted">Only an admin can start work.</p>}
      </div>
    </Dialog>
  );
}

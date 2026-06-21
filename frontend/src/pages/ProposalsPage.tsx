import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  batchShipProposals,
  fetchProposals,
  rescoreProposal,
  shipProposal,
  type AgentProposal,
} from "@/api/agent";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { PageError } from "@/components/PageError";

const PROPOSALS_KEY = ["agent", "proposals"] as const;

export function ProposalsPage() {
  const qc = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery<AgentProposal[]>({
    queryKey: PROPOSALS_KEY,
    queryFn: fetchProposals,
    refetchInterval: 30_000,
  });

  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const ship = useMutation({
    mutationFn: shipProposal,
    onMutate: async (id: string) => {
      await qc.cancelQueries({ queryKey: PROPOSALS_KEY });
      const prev = qc.getQueryData<AgentProposal[]>(PROPOSALS_KEY);
      qc.setQueryData<AgentProposal[]>(PROPOSALS_KEY, (cur) =>
        (cur ?? []).map((p) => (p.id === id ? { ...p, status: "shipped" } : p)),
      );
      return { prev };
    },
    onError: (_err, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(PROPOSALS_KEY, ctx.prev);
      toast.error("Failed to ship proposal");
    },
    onSuccess: () => toast.success("Proposal shipped"),
    onSettled: () => qc.invalidateQueries({ queryKey: PROPOSALS_KEY }),
  });

  const rescore = useMutation({
    mutationFn: rescoreProposal,
    onSuccess: (updated) => {
      qc.setQueryData<AgentProposal[]>(PROPOSALS_KEY, (cur) =>
        (cur ?? []).map((p) => (p.id === updated.id ? updated : p)),
      );
      toast.success("Proposal rescored");
    },
    onError: () => toast.error("Failed to rescore proposal"),
  });

  const batchShip = useMutation({
    mutationFn: batchShipProposals,
    onMutate: async (ids: string[]) => {
      await qc.cancelQueries({ queryKey: PROPOSALS_KEY });
      const prev = qc.getQueryData<AgentProposal[]>(PROPOSALS_KEY);
      const idSet = new Set(ids);
      qc.setQueryData<AgentProposal[]>(PROPOSALS_KEY, (cur) =>
        (cur ?? []).map((p) => (idSet.has(p.id) ? { ...p, status: "shipped" } : p)),
      );
      return { prev };
    },
    onError: (_err, _ids, ctx) => {
      if (ctx?.prev) qc.setQueryData(PROPOSALS_KEY, ctx.prev);
      toast.error("Failed to batch-ship proposals");
    },
    onSuccess: (res) => {
      setSelected(new Set());
      toast.success(`Shipped ${res.shipped.length} proposal(s)`);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: PROPOSALS_KEY }),
  });

  const proposals = data ?? [];
  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  function toggleSelected(id: string) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (isError) {
    return <PageError message="Failed to load proposals" onRetry={() => refetch()} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Proposals</h1>
        <Button
          size="sm"
          disabled={selectedIds.length === 0 || batchShip.isPending}
          onClick={() => batchShip.mutate(selectedIds)}
        >
          Ship selected ({selectedIds.length})
        </Button>
      </div>

      {isLoading && (
        <div className="text-sm text-white/40">Loading proposals…</div>
      )}

      {!isLoading && proposals.length === 0 && (
        <div className="athanor-card p-6 text-sm text-white/60">
          No proposals from the scanner yet.
        </div>
      )}

      {proposals.length > 0 && (
        <div
          className="athanor-card overflow-hidden"
          data-testid="proposals-list"
        >
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-white/[0.06] text-white/40 text-[10px] uppercase tracking-wider">
                <th scope="col" className="w-8 px-3 py-2"></th>
                <th scope="col" className="text-left py-2 font-medium">Title</th>
                <th scope="col" className="text-left py-2 font-medium">Repo</th>
                <th scope="col" className="text-right py-2 font-medium">Severity</th>
                <th scope="col" className="text-left py-2 font-medium pl-3">Status</th>
                <th scope="col" className="text-right py-2 font-medium pr-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => (
                <ProposalRow
                  key={p.id}
                  proposal={p}
                  checked={selected.has(p.id)}
                  onToggleSelect={() => toggleSelected(p.id)}
                  onShip={() => ship.mutate(p.id)}
                  onRescore={() => rescore.mutate(p.id)}
                  shipPending={ship.isPending && ship.variables === p.id}
                  rescorePending={rescore.isPending && rescore.variables === p.id}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

interface ProposalRowProps {
  proposal: AgentProposal;
  checked: boolean;
  onToggleSelect: () => void;
  onShip: () => void;
  onRescore: () => void;
  shipPending: boolean;
  rescorePending: boolean;
}

function ProposalRow({
  proposal,
  checked,
  onToggleSelect,
  onShip,
  onRescore,
  shipPending,
  rescorePending,
}: ProposalRowProps) {
  const shipped = proposal.status === "shipped";
  return (
    <tr
      data-testid={`proposal-row-${proposal.id}`}
      className="border-b border-white/[0.04] hover:bg-cyan-500/[0.02] transition-colors"
    >
      <td className="px-3 py-2">
        <input
          type="checkbox"
          aria-label={`Select ${proposal.id}`}
          checked={checked}
          onChange={onToggleSelect}
          disabled={shipped}
        />
      </td>
      <td className="py-2 text-white/90 truncate max-w-[420px]" title={proposal.title}>
        {proposal.title}
      </td>
      <td className="py-2 font-mono text-white/70 truncate max-w-[200px]" title={proposal.repo}>
        {proposal.repo ?? "—"}
      </td>
      <td className="py-2 text-right font-mono tabular-nums text-white/80">
        {proposal.severity ?? "—"}
      </td>
      <td className="py-2 pl-3">
        <Badge tone={shipped ? "success" : "neutral"}>
          {proposal.status ?? "pending"}
        </Badge>
      </td>
      <td className="py-2 pr-3 text-right space-x-2">
        <Button
          size="sm"
          variant="outline"
          aria-label={`Rescore ${proposal.id}`}
          onClick={onRescore}
          disabled={rescorePending}
        >
          Rescore
        </Button>
        <Button
          size="sm"
          aria-label={`Ship ${proposal.id}`}
          onClick={onShip}
          disabled={shipped || shipPending}
        >
          Ship
        </Button>
      </td>
    </tr>
  );
}

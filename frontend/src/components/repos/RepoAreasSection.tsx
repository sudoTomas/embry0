import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { apiErrorMessage } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useAreasProposal, useMapAreas, useRepoAreas } from "@/hooks/useRepoAreas";
import { formatDateTime } from "@/lib/utils";
import { useCanOperate } from "@/stores/authStore";
import { AreasEditor } from "./AreasEditor";
import { AreasProposalCard } from "./AreasProposalCard";

/**
 * A repository's areas (spec 2026-11-10): the areas embry0 keeps for it, an editor of their document, the map
 * waiting for a person, and the agent that maps them. The repository carries no file for them.
 */
export function RepoAreasSection({ repo }: { repo: string }) {
  const areas = useRepoAreas(repo);
  const proposal = useAreasProposal(repo, !!areas.data?.proposal);
  const map = useMapAreas(repo);
  const canOperate = useCanOperate();
  const [runWith, setRunWith] = useState<RunWith>({});
  // null: not editing; a string: the text the editor opened on.
  const [editing, setEditing] = useState<string | null>(null);
  const latest = areas.data?.latest_job ?? null;
  const running = latest?.live === true || areas.data?.proposal?.status === "running";

  const start = () =>
    map.mutate(runWith, {
      onSuccess: (done) =>
        toast.success(`Mapping started (${done.job_id}); its areas wait here for you when it finishes.`),
      onError: (e) => toastApiError(e, "Mapping did not start"),
    });

  if (areas.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!areas.data) {
    return (
      <div className="space-y-2" data-testid="repo-areas-error">
        <p role="alert" className="text-sm text-warning">
          The areas could not be read{areas.error ? `: ${apiErrorMessage(areas.error)}` : "."}
        </p>
        <Button size="sm" variant="secondary" disabled={areas.isFetching} onClick={() => void areas.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  const data = areas.data;
  const rows = data.areas;

  return (
    <div className="space-y-3" data-testid="repo-areas">
      {proposal.data && (
        <AreasProposalCard
          repo={repo}
          proposal={proposal.data}
          onEdit={(text) => setEditing(text)}
          canDecide={canOperate}
        />
      )}

      {editing !== null ? (
        <AreasEditor
          repo={repo}
          stored={data.yaml_text}
          generation={data.generation}
          initialText={editing || undefined}
          onDone={() => setEditing(null)}
        />
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          This repository names no areas yet: everything embry0 says about it names only the repository.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="data-table">
            <thead>
              <tr>
                <th>Area</th>
                <th>Paths</th>
                <th className="hidden sm:table-cell">Logs</th>
                <th className="hidden sm:table-cell">Owners</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((area) => (
                <tr key={area.slug}>
                  <td className="font-medium">{area.name}</td>
                  <td className="font-mono text-xs break-all">{area.paths.join(", ")}</td>
                  <td className="hidden font-mono text-xs sm:table-cell">{area.logs.join(", ") || "—"}</td>
                  <td className="hidden text-xs sm:table-cell">{area.owners.join(", ") || "the repository's"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.imported && (
        <p className="text-xs text-muted-foreground" data-testid="repo-areas-imported">
          Imported from the repository&apos;s <code className="font-mono">.embry0/areas.yml</code>
          {data.imported.at ? ` on ${formatDateTime(data.imported.at)}` : ""}. embry0 no longer reads that file; you can
          delete it once the next release is in.
        </p>
      )}

      {editing === null && canOperate && (
        <Button size="sm" variant="secondary" onClick={() => setEditing("")}>
          {rows.length || data.yaml_text ? "Edit areas" : "Write areas"}
        </Button>
      )}

      {latest && (
        <p className="text-xs text-muted-foreground" data-testid="repo-areas-latest">
          {running ? "Mapping now" : `Last mapped: ${latest.status}`} ·{" "}
          <Link className="underline hover:text-fg" to={`/jobs/${latest.job_id}`}>
            {latest.job_id}
          </Link>
          {latest.pr_url && (
            <>
              {" · "}
              <a className="underline hover:text-fg" href={latest.pr_url} target="_blank" rel="noreferrer">
                its pull request
              </a>
            </>
          )}
        </p>
      )}

      {canOperate ? (
        <div className="space-y-2">
          <Button size="sm" disabled={running || map.isPending} onClick={start}>
            {rows.length ? "Map areas again" : "Map areas"}
          </Button>
          <p className="text-xs text-muted-foreground">
            An agent reads the repository and proposes its areas; nothing changes until you apply them, and nothing
            is written to the repository. It is a paid job.
          </p>
          <RunWithControl value={runWith} onChange={setRunWith} repo={repo} />
        </div>
      ) : (
        <p className="text-xs text-muted-foreground" data-testid="repo-areas-read-only">
          Changing a repository&apos;s areas, mapping them and applying a map take an operate key.
        </p>
      )}
    </div>
  );
}

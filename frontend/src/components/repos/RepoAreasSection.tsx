import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useMapAreas, useRepoAreas } from "@/hooks/useRepoAreas";

/** Map areas (spec 2026-10-25): what the repository's areas file names, and the agent that writes it. */
export function RepoAreasSection({ repo }: { repo: string }) {
  const areas = useRepoAreas(repo);
  const map = useMapAreas(repo);
  const [runWith, setRunWith] = useState<RunWith>({});
  const latest = areas.data?.latest_job ?? null;
  const running = latest?.live === true;

  const start = () =>
    map.mutate(runWith, {
      onSuccess: (done) => toast.success(`Mapping started (${done.job_id}); its pull request opens when it finishes.`),
      onError: (e) => toastApiError(e, "Mapping did not start"),
    });

  if (areas.isLoading) return <Skeleton className="h-24 w-full" />;
  const rows = areas.data?.areas ?? [];

  return (
    <div className="space-y-3" data-testid="repo-areas">
      {rows.length === 0 ? (
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

      <div className="space-y-2">
        <Button size="sm" disabled={running || map.isPending} onClick={start}>
          {rows.length ? "Map areas again" : "Map areas"}
        </Button>
        <p className="text-xs text-muted-foreground">
          An agent reads the repository and opens a pull request with {areas.data?.path ?? ".embry0/areas.yml"}; nothing
          changes until it merges. It is a paid job.
        </p>
        <RunWithControl value={runWith} onChange={setRunWith} repo={repo} />
      </div>
    </div>
  );
}

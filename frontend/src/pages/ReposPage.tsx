import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, GitPullRequest, GitMerge, Upload } from "lucide-react";
import { toast } from "sonner";
import {
  fetchRepos,
  pushRepo,
  pushRepoPr,
  mergeRepoPr,
  type AgentRepo,
} from "@/api/agent";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

const QUERY_KEY = ["agent", "repos"] as const;

export function ReposPage() {
  const qc = useQueryClient();
  const { data: repos, isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: fetchRepos,
    refetchInterval: 30_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: QUERY_KEY });

  const push = useMutation({
    mutationFn: (slug: string) => pushRepo(slug),
    onSuccess: (_d, slug) => {
      toast.success(`Pushed ${slug}`);
      invalidate();
    },
    onError: (e, slug) => toast.error(`Push ${slug} failed: ${e.message}`),
  });

  const pushPr = useMutation({
    mutationFn: (slug: string) => pushRepoPr(slug),
    onSuccess: (_d, slug) => {
      toast.success(`Push PR opened for ${slug}`);
      invalidate();
    },
    onError: (e, slug) => toast.error(`Push PR ${slug} failed: ${e.message}`),
  });

  const mergePr = useMutation({
    mutationFn: (slug: string) => mergeRepoPr(slug),
    onSuccess: (_d, slug) => {
      toast.success(`Merged PR for ${slug}`);
      invalidate();
    },
    onError: (e, slug) => toast.error(`Merge PR ${slug} failed: ${e.message}`),
  });

  const confirmAndRun = (
    verb: string,
    slug: string,
    mutate: (slug: string) => void,
  ) => {
    if (!window.confirm(`${verb} ${slug}?`)) return;
    mutate(slug);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Repos</h1>
      </div>

      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="py-12 text-center text-white/40 text-sm">
              Loading...
            </div>
          ) : !repos || repos.length === 0 ? (
            <div className="py-12 text-center text-white/40 text-sm">
              No repos
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">
                    Repo
                  </th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">
                    Branch
                  </th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">
                    State
                  </th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">
                    PR
                  </th>
                  <th className="text-right px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {repos.map((repo) => (
                  <RepoRow
                    key={repo.slug}
                    repo={repo}
                    onPush={() =>
                      confirmAndRun("Push", repo.slug, push.mutate)
                    }
                    onPushPr={() =>
                      confirmAndRun("Push PR for", repo.slug, pushPr.mutate)
                    }
                    onMergePr={() =>
                      confirmAndRun("Merge PR for", repo.slug, mergePr.mutate)
                    }
                    pushing={push.isPending}
                    pushingPr={pushPr.isPending}
                    mergingPr={mergePr.isPending}
                  />
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

interface RepoRowProps {
  repo: AgentRepo;
  onPush: () => void;
  onPushPr: () => void;
  onMergePr: () => void;
  pushing: boolean;
  pushingPr: boolean;
  mergingPr: boolean;
}

function RepoRow({
  repo,
  onPush,
  onPushPr,
  onMergePr,
  pushing,
  pushingPr,
  mergingPr,
}: RepoRowProps) {
  const hasPr = typeof repo.pr_number === "number";
  return (
    <tr
      data-testid={`repo-row-${repo.slug}`}
      className="hover:bg-white/[0.02] transition-colors"
    >
      <td className="px-6 py-3">
        <span className="text-sm font-mono text-white/80">{repo.slug}</span>
      </td>
      <td className="px-6 py-3">
        <span className="inline-flex items-center gap-1.5 text-sm font-mono text-white/60">
          <GitBranch className="w-3.5 h-3.5 text-white/40" />
          {repo.branch ?? "—"}
        </span>
      </td>
      <td className="px-6 py-3">
        <RepoState repo={repo} />
      </td>
      <td className="px-6 py-3">
        {hasPr ? (
          repo.pr_url ? (
            <a
              href={repo.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-mono text-cyan-400 hover:underline"
            >
              #{repo.pr_number}
            </a>
          ) : (
            <span className="text-sm font-mono text-white/60">
              #{repo.pr_number}
            </span>
          )
        ) : (
          <span className="text-sm text-white/30">—</span>
        )}
      </td>
      <td className="px-6 py-3">
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            aria-label={`Push ${repo.slug}`}
            disabled={pushing}
            onClick={onPush}
          >
            <Upload className="w-3.5 h-3.5" />
            Push
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Push PR ${repo.slug}`}
            disabled={pushingPr}
            onClick={onPushPr}
          >
            <GitPullRequest className="w-3.5 h-3.5" />
            Push PR
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Merge PR ${repo.slug}`}
            disabled={!hasPr || mergingPr}
            onClick={onMergePr}
          >
            <GitMerge className="w-3.5 h-3.5" />
            Merge PR
          </Button>
        </div>
      </td>
    </tr>
  );
}

function RepoState({ repo }: { repo: AgentRepo }) {
  const ahead = repo.ahead ?? 0;
  const behind = repo.behind ?? 0;
  return (
    <div className="flex items-center gap-1.5">
      {repo.dirty && <Badge tone="warning">dirty</Badge>}
      {ahead > 0 && <Badge tone="info">↑{ahead}</Badge>}
      {behind > 0 && <Badge tone="warning">↓{behind}</Badge>}
      {!repo.dirty && ahead === 0 && behind === 0 && (
        <span className="text-sm text-white/40">clean</span>
      )}
    </div>
  );
}

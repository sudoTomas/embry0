import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { setRepoDockerAccess } from "@/api/repos";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useAuthStore } from "@/stores/authStore";
import { toastApiError } from "@/api/errorToast";

/** What the grant hands over (#522). Shown wherever the grant can be given. */
export const DOCKER_ACCESS_WARNING =
  "Code from this repo can control the shared Docker daemon, including the proxies holding your tokens.";

/** #522: the per-repo admin grant of the shared Docker daemon (DinD).
 * A sandbox profile with DinD only asks for the daemon; a sandbox gets it
 * when its repo holds this grant. */
export function RepoDockerAccessSection({ repo, dockerAccess }: { repo: string; dockerAccess: boolean }) {
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (enabled: boolean) => setRepoDockerAccess(repo, enabled),
    onSuccess: (row) => {
      toast.success(row.docker_access ? "Docker access granted" : "Docker access revoked");
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
    },
    onError: (error: unknown) => toastApiError(error, "Could not change Docker access"),
  });

  function onToggle() {
    if (mutation.isPending) return;
    const enabling = !dockerAccess;
    // Granting is the dangerous direction: say what it hands over, every time.
    if (enabling && !window.confirm(`Grant ${repo} Docker access?\n\n${DOCKER_ACCESS_WARNING}`)) return;
    mutation.mutate(enabling);
  }

  return (
    <Card className="space-y-2 p-4" data-testid="repo-docker-access">
      <div className="flex flex-wrap items-center gap-3">
        {dockerAccess ? (
          <Badge tone="warning">Docker access granted</Badge>
        ) : (
          <Badge tone="neutral">No Docker access</Badge>
        )}
        {isAdmin ? (
          <Button
            size="sm"
            variant={dockerAccess ? "outline" : "destructive"}
            disabled={mutation.isPending}
            onClick={onToggle}
          >
            {dockerAccess ? "Revoke Docker access" : "Grant Docker access"}
          </Button>
        ) : (
          <span className="text-xs text-white/50">Only an admin can change this.</span>
        )}
      </div>
      <p className="text-sm text-white/80">{DOCKER_ACCESS_WARNING}</p>
      <p className="text-xs text-white/50">
        Needed only when a QA app boots with <code>mode: dind</code> (docker compose) on a sandbox profile with
        DinD, such as <code>qa-jvm</code>. A profile never grants it on its own; without the grant, a sandbox on
        such a profile is refused before it starts.
      </p>
    </Card>
  );
}

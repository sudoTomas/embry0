import { useMutation, useQueryClient } from "@tanstack/react-query";
import { UsersRound, LogOut, Ban, CircleCheck } from "lucide-react";
import { toast } from "sonner";
import { useUsers } from "@/hooks/useUsers";
import { setUserDisabled, forceLogoutUser, type AdminUser } from "@/api/users";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { statusLabel } from "@/lib/status";
import { formatDate } from "@/lib/utils";

const TIER_TONES: Record<AdminUser["tier"], BadgeTone> = {
  admin: "gold",
  operator: "neutral",
  none: "neutral",
};

/**
 * Users admin (RAV-1056 Phase A). Rows are JIT-mirrored from SSO logins —
 * roles/tiers are assigned in Auth0 and cached here at each login. The
 * embry0-side controls are Disable (deny regardless of Auth0 role) and
 * Force logout (session-epoch bump — invalidates every cookie the user
 * holds). Admin-tier only; the backend enforces it independently of nav
 * visibility.
 */
export function UsersAdminPage() {
  const users = useUsers();
  const queryClient = useQueryClient();
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["admin-users"] });

  const disableMut = useMutation({
    mutationFn: ({ id, disabled }: { id: string; disabled: boolean }) => setUserDisabled(id, disabled),
    onSuccess: (row) => {
      toast.success(`${row.email} ${row.disabled ? "disabled" : "enabled"}`);
      invalidate();
    },
    onError: () => toast.error("Update failed"),
  });

  const logoutMut = useMutation({
    mutationFn: (id: string) => forceLogoutUser(id),
    onSuccess: (row) => {
      toast.success(`${row.email} signed out everywhere`);
      invalidate();
    },
    onError: () => toast.error("Force logout failed"),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <UsersRound size={18} className="text-primary" />
          <h1 className="text-xl font-bold">Users</h1>
          {users.data && (
            <span className="text-sm text-muted-foreground">{users.data.total}</span>
          )}
        </div>
      </div>
      <p className="text-sm text-muted-foreground">
        Mirrored from SSO logins. Tiers come from Auth0 roles
        (<code>embry0-operator</code> / <code>embry0-admin</code>) and refresh at each login —
        assign roles in Auth0, not here. Disable blocks access regardless of role;
        Force logout invalidates every active session.
      </p>

      {users.isLoading && <TableSkeleton rows={4} />}
      {users.isError && <PageError message="Could not load users" onRetry={() => users.refetch()} />}
      {users.data && users.data.users.length === 0 && (
        <EmptyState title="No users yet" description="Rows appear after the first SSO login." />
      )}

      {users.data && users.data.users.length > 0 && (
        <div className="embry0-card overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>User</th>
                <th className="hidden sm:table-cell">Tier</th>
                <th>Status</th>
                <th className="hidden sm:table-cell">Logins</th>
                <th className="hidden sm:table-cell">Last login</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.data.users.map((u) => (
                <tr key={u.id}>
                  {/* Below sm the email truncates and tier, logins and last
                      login step aside, so status and the actions stay in view. */}
                  <td className="w-full max-w-0 sm:w-auto sm:max-w-none">
                    <div className="truncate text-foreground" title={u.email}>{u.email}</div>
                    {u.name && <div className="truncate text-xs text-muted-foreground">{u.name}</div>}
                  </td>
                  <td className="hidden sm:table-cell">
                    <Badge tone={TIER_TONES[u.tier]}>{statusLabel(u.tier)}</Badge>
                  </td>
                  <td>
                    {u.disabled ? <Badge tone="error">Disabled</Badge> : <Badge tone="success">Active</Badge>}
                  </td>
                  <td className="hidden font-mono text-xs sm:table-cell">{u.login_count}</td>
                  <td className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                    {u.last_login_at ? formatDate(u.last_login_at) : "—"}
                  </td>
                  <td>
                    <div className="flex items-center justify-end gap-1.5">
                      <Button
                        variant="ghost"
                        size="xs"
                        title="Sign this user out of every session"
                        disabled={logoutMut.isPending}
                        onClick={() => {
                          if (window.confirm(`Force ${u.email} out of all sessions?`)) {
                            logoutMut.mutate(u.id);
                          }
                        }}
                      >
                        <LogOut size={14} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="xs"
                        title={u.disabled ? "Re-enable access" : "Disable access (regardless of Auth0 role)"}
                        disabled={disableMut.isPending}
                        onClick={() => {
                          const verb = u.disabled ? "Re-enable" : "Disable";
                          if (window.confirm(`${verb} ${u.email}?`)) {
                            disableMut.mutate({ id: u.id, disabled: !u.disabled });
                          }
                        }}
                      >
                        {u.disabled ? <CircleCheck size={14} /> : <Ban size={14} className="text-destructive" />}
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

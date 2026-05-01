import { useNavigate, Link } from "react-router";
import { Box, Trash2, Plus, Cpu, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { IconBox } from "@/components/ui/IconBox";
import { useSandboxProfiles, useDeleteSandboxProfile } from "@/hooks/useSandboxProfiles";
import type { SandboxProfile } from "@/api/sandbox-profiles";
import { toast } from "sonner";

export function SandboxesPage() {
  const { data: profiles, isLoading, isError } = useSandboxProfiles();
  const deleteMutation = useDeleteSandboxProfile();
  const navigate = useNavigate();

  const handleDelete = (e: React.MouseEvent, name: string) => {
    e.stopPropagation();
    if (!confirm(`Delete sandbox profile "${name}"?`)) return;
    deleteMutation.mutate(name, {
      onSuccess: () => toast.success(`Deleted "${name}"`),
      onError: (err) => toast.error(`Failed to delete: ${err.message}`),
    });
  };

  if (isError) {
    return (
      <div className="p-8 text-center text-red-400">
        Failed to load sandbox profiles.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Sandboxes</h1>
        <Link to="/sandboxes/new">
          <Button size="sm" className="gap-1.5">
            <Plus className="w-4 h-4" />
            New Profile
          </Button>
        </Link>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="text-muted-foreground">Loading...</div>
      )}

      {/* Empty state */}
      {!isLoading && profiles && profiles.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center text-white/40 space-y-3">
          <IconBox icon={Box} color="#06b6d4" size="lg" />
          <p className="text-sm">No sandbox profiles yet.</p>
          <Link to="/sandboxes/new">
            <Button variant="outline" size="sm">Create your first profile</Button>
          </Link>
        </div>
      )}

      {/* Grid */}
      {profiles && profiles.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {profiles.map((profile: SandboxProfile) => (
            <SandboxCard
              key={profile.name}
              profile={profile}
              onClick={() => navigate(`/sandboxes/${profile.name}`)}
              onDelete={(e) => handleDelete(e, profile.name)}
              isDeleting={deleteMutation.isPending && deleteMutation.variables === profile.name}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface SandboxCardProps {
  profile: SandboxProfile;
  onClick: () => void;
  onDelete: (e: React.MouseEvent) => void;
  isDeleting: boolean;
}

function SandboxCard({ profile, onClick, onDelete, isDeleting }: SandboxCardProps) {
  return (
    <Card
      className="cursor-pointer hover:border-cyan-500/20 transition-colors group"
      onClick={onClick}
    >
      <CardContent className="p-5 space-y-4">
        {/* Name row */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <IconBox icon={Box} color="#06b6d4" size="sm" />
            <span className="font-mono text-sm font-semibold text-white truncate">
              {profile.name}
            </span>
          </div>
          {!profile.is_builtin && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity hover:text-destructive"
              onClick={onDelete}
              disabled={isDeleting}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>

        {/* Base image */}
        <div className="text-xs text-white/50 font-mono truncate">
          {profile.base_image}
        </div>

        {/* Description */}
        {profile.description && (
          <p className="text-xs text-white/40 line-clamp-2">{profile.description}</p>
        )}

        {/* Badges */}
        {(profile.dind_enabled || profile.is_builtin) && (
          <div className="flex flex-wrap gap-2">
            {profile.dind_enabled && (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 font-mono">
                DinD
              </span>
            )}
            {profile.is_builtin && (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-white/[0.05] text-white/50 border border-white/[0.06] font-mono">
                builtin
              </span>
            )}
          </div>
        )}

        {/* Resource chips */}
        <div className="flex flex-wrap gap-2">
          <ResourceChip icon={<Cpu className="w-3 h-3" />} label={`${profile.cpus} CPU`} />
          <ResourceChip icon={null} label={profile.memory} />
          {profile.pids_limit != null && (
            <ResourceChip icon={null} label={`${profile.pids_limit} pids`} />
          )}
        </div>

        {/* Timeouts */}
        <div className="flex items-center gap-1.5 text-xs text-white/40">
          <Clock className="w-3 h-3" />
          <span>
            Agent {profile.agent_timeout_seconds}s / Container {profile.container_timeout_seconds}s
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function ResourceChip({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white/[0.05] border border-white/[0.06] text-xs text-white/60">
      {icon}
      {label}
    </span>
  );
}

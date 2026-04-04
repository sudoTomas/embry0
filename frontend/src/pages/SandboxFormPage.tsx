import { useState, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { TagInput } from "@/components/ui/TagInput";
import {
  useSandboxProfile,
  useCreateSandboxProfile,
  useUpdateSandboxProfile,
} from "@/hooks/useSandboxProfiles";
import type { SandboxProfile } from "@/api/sandbox-profiles";
import { toast } from "sonner";

const DEFAULT_PROFILE: Omit<SandboxProfile, "created_at" | "updated_at"> = {
  name: "",
  base_image: "legion-sandbox:latest",
  additional_packages: [],
  setup_commands: [],
  memory: "8g",
  cpus: "4",
  pids_limit: 256,
  cap_drop: ["ALL"],
  cap_add: [],
  security_opt: ["no-new-privileges"],
  agent_timeout_seconds: 300,
  container_timeout_seconds: 3600,
};

export function SandboxFormPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const isEditMode = !!name && name !== "new";

  const { data: existing, isLoading } = useSandboxProfile(isEditMode ? name : null);
  const createMutation = useCreateSandboxProfile();
  const updateMutation = useUpdateSandboxProfile();

  const [form, setForm] = useState<Omit<SandboxProfile, "created_at" | "updated_at">>(
    DEFAULT_PROFILE
  );

  // Populate form when editing
  useEffect(() => {
    if (isEditMode && existing) {
      const { created_at: _ca, updated_at: _ua, ...rest } = existing;
      setForm(rest);
    }
  }, [isEditMode, existing]);

  const setField = <K extends keyof typeof form>(
    field: K,
    value: (typeof form)[K]
  ) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleSave = () => {
    if (!form.name.trim()) {
      toast.error("Profile name is required");
      return;
    }

    if (isEditMode) {
      updateMutation.mutate(form, {
        onSuccess: () => {
          toast.success(`Updated "${form.name}"`);
          navigate("/sandboxes");
        },
        onError: (err) => toast.error(`Failed to update: ${err.message}`),
      });
    } else {
      createMutation.mutate(form, {
        onSuccess: () => {
          toast.success(`Created "${form.name}"`);
          navigate("/sandboxes");
        },
        onError: (err) => toast.error(`Failed to create: ${err.message}`),
      });
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  if (isEditMode && isLoading) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Link to="/sandboxes">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <h1 className="text-2xl font-bold">
            {isEditMode ? `Edit "${name}"` : "New Sandbox Profile"}
          </h1>
        </div>
        <div className="flex gap-2">
          <Link to="/sandboxes">
            <Button variant="outline" disabled={isPending}>
              Cancel
            </Button>
          </Link>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending ? "Saving..." : "Save Profile"}
          </Button>
        </div>
      </div>

      {/* Section 1: Image & Packages */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Image &amp; Packages</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="name">Profile Name</Label>
              <Input
                id="name"
                value={form.name}
                onChange={(e) => setField("name", e.target.value)}
                disabled={isEditMode}
                placeholder="e.g. python-dev"
                className="mt-1"
              />
              {isEditMode && (
                <p className="text-xs text-white/30 mt-1">Name cannot be changed after creation.</p>
              )}
            </div>
            <div>
              <Label htmlFor="base_image">Base Image</Label>
              <Input
                id="base_image"
                value={form.base_image}
                onChange={(e) => setField("base_image", e.target.value)}
                placeholder="legion-sandbox:latest"
                className="mt-1"
              />
            </div>
          </div>
          <div>
            <Label>Additional Packages</Label>
            <div className="mt-1">
              <TagInput
                value={form.additional_packages}
                onChange={(tags) => setField("additional_packages", tags)}
                placeholder="Add package..."
              />
            </div>
            <p className="text-xs text-white/30 mt-1">Press Enter to add. These are installed at container startup.</p>
          </div>
          <div>
            <Label>Setup Commands</Label>
            <div className="mt-1">
              <TagInput
                value={form.setup_commands}
                onChange={(tags) => setField("setup_commands", tags)}
                placeholder="Add command..."
              />
            </div>
            <p className="text-xs text-white/30 mt-1">Shell commands run after package installation.</p>
          </div>
        </CardContent>
      </Card>

      {/* Section 2: Resources */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Resources</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="memory">Memory</Label>
              <Input
                id="memory"
                value={form.memory}
                onChange={(e) => setField("memory", e.target.value)}
                placeholder="8g"
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">e.g. 512m, 4g, 8g</p>
            </div>
            <div>
              <Label htmlFor="cpus">CPUs</Label>
              <Input
                id="cpus"
                value={form.cpus}
                onChange={(e) => setField("cpus", e.target.value)}
                placeholder="4"
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">CPU limit (e.g. 0.5, 2, 4)</p>
            </div>
            <div>
              <Label htmlFor="pids_limit">PIDs Limit</Label>
              <Input
                id="pids_limit"
                type="number"
                min={1}
                max={65535}
                value={form.pids_limit}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (!isNaN(n)) setField("pids_limit", Math.min(65535, Math.max(1, n)));
                }}
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">1–65535</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Section 3: Security */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Security</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label>cap_drop</Label>
              <div className="mt-1">
                <TagInput
                  value={form.cap_drop}
                  onChange={(tags) => setField("cap_drop", tags)}
                  placeholder="Add capability..."
                />
              </div>
              <p className="text-xs text-white/30 mt-1">Linux capabilities to drop (default: ALL)</p>
            </div>
            <div>
              <Label>cap_add</Label>
              <div className="mt-1">
                <TagInput
                  value={form.cap_add}
                  onChange={(tags) => setField("cap_add", tags)}
                  placeholder="Add capability..."
                />
              </div>
              <p className="text-xs text-white/30 mt-1">Linux capabilities to add back selectively</p>
            </div>
          </div>
          <div>
            <Label>security_opt</Label>
            <div className="mt-1">
              <TagInput
                value={form.security_opt}
                onChange={(tags) => setField("security_opt", tags)}
                placeholder="Add option..."
              />
            </div>
            <p className="text-xs text-white/30 mt-1">e.g. no-new-privileges, seccomp=profile.json</p>
          </div>
        </CardContent>
      </Card>

      {/* Section 4: Timeouts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Timeouts</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="agent_timeout_seconds">Agent Timeout (seconds)</Label>
              <Input
                id="agent_timeout_seconds"
                type="number"
                min={1}
                value={form.agent_timeout_seconds}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (!isNaN(n) && n > 0) setField("agent_timeout_seconds", n);
                }}
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">Max time for the agent to complete its task</p>
            </div>
            <div>
              <Label htmlFor="container_timeout_seconds">Container Timeout (seconds)</Label>
              <Input
                id="container_timeout_seconds"
                type="number"
                min={1}
                value={form.container_timeout_seconds}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (!isNaN(n) && n > 0) setField("container_timeout_seconds", n);
                }}
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">Max lifetime of the container</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

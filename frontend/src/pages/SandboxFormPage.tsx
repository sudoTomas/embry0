import { useState, useEffect } from "react";
import { useParams, useNavigate, Link } from "react-router";
import { ArrowLeft, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { TagInput } from "@/components/ui/TagInput";
import {
  useSandboxProfile,
  useCreateSandboxProfile,
  useUpdateSandboxProfile,
  useResetSandboxProfile,
} from "@/hooks/useSandboxProfiles";
import type { SandboxProfile } from "@/api/sandbox-profiles";
import { toast } from "sonner";

type ProfileForm = Omit<SandboxProfile, "created_at" | "updated_at" | "is_builtin">;

const DEFAULT_PROFILE: ProfileForm = {
  name: "",
  base_image: "athanor-sandbox:latest",
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
  description: "",
  dind_enabled: false,
  idle_timeout_seconds: 600,
  extra_networks: [],
  env_defaults: {},
};

export function SandboxFormPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const isEditMode = !!name && name !== "new";

  const { data: existing, isLoading } = useSandboxProfile(isEditMode ? name : null);
  const createMutation = useCreateSandboxProfile();
  const updateMutation = useUpdateSandboxProfile();
  const resetMutation = useResetSandboxProfile();

  const [form, setForm] = useState<ProfileForm>(DEFAULT_PROFILE);
  // Local raw text for env_defaults JSON so the user can type freely (incl.
  // intermediate invalid JSON) without losing characters. We only push to
  // form.env_defaults when the text parses to a valid plain object.
  const [envDefaultsText, setEnvDefaultsText] = useState<string>(
    JSON.stringify(DEFAULT_PROFILE.env_defaults, null, 2)
  );
  const [envDefaultsValid, setEnvDefaultsValid] = useState<boolean>(true);

  const isBuiltin = isEditMode && !!existing?.is_builtin;

  // Populate form when editing
  useEffect(() => {
    if (isEditMode && existing) {
      const { created_at: _ca, updated_at: _ua, is_builtin: _ib, ...rest } = existing;
      setForm(rest);
      setEnvDefaultsText(JSON.stringify(rest.env_defaults ?? {}, null, 2));
      setEnvDefaultsValid(true);
    }
  }, [isEditMode, existing]);

  const setField = <K extends keyof ProfileForm>(
    field: K,
    value: ProfileForm[K]
  ) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleEnvDefaultsChange = (text: string) => {
    setEnvDefaultsText(text);
    const trimmed = text.trim();
    if (trimmed === "") {
      setField("env_defaults", {});
      setEnvDefaultsValid(true);
      return;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        !Array.isArray(parsed) &&
        Object.values(parsed).every((v) => typeof v === "string")
      ) {
        setField("env_defaults", parsed as Record<string, string>);
        setEnvDefaultsValid(true);
      } else {
        setEnvDefaultsValid(false);
      }
    } catch {
      setEnvDefaultsValid(false);
    }
  };

  const handleSave = () => {
    if (!form.name.trim()) {
      toast.error("Profile name is required");
      return;
    }
    if (!envDefaultsValid) {
      toast.error("Env Defaults must be a valid JSON object of string values");
      return;
    }

    // The mutations expect a SandboxProfile shape; include is_builtin=false on
    // create. On update the server ignores the field but it satisfies the type.
    const payload: SandboxProfile = {
      ...form,
      is_builtin: existing?.is_builtin ?? false,
    };

    if (isEditMode) {
      updateMutation.mutate(payload, {
        onSuccess: () => {
          toast.success(`Updated "${form.name}"`);
          navigate("/sandboxes");
        },
        onError: (err) => toast.error(`Failed to update: ${err.message}`),
      });
    } else {
      createMutation.mutate(payload, {
        onSuccess: () => {
          toast.success(`Created "${form.name}"`);
          navigate("/sandboxes");
        },
        onError: (err) => toast.error(`Failed to create: ${err.message}`),
      });
    }
  };

  const handleReset = () => {
    if (!name) return;
    if (!confirm(`Reset "${name}" to its built-in defaults?`)) return;
    resetMutation.mutate(name, {
      onSuccess: () => {
        toast.success(`Reset "${name}"`);
        navigate("/sandboxes");
      },
      onError: (err) => toast.error(`Failed to reset: ${(err as Error).message}`),
    });
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
            <Button variant="outline" disabled={isPending || resetMutation.isPending}>
              Cancel
            </Button>
          </Link>
          {isBuiltin ? (
            <Button
              variant="destructive"
              onClick={handleReset}
              disabled={isPending || resetMutation.isPending}
            >
              {resetMutation.isPending ? "Resetting..." : "Reset to Default"}
            </Button>
          ) : (
            <Button onClick={handleSave} disabled={isPending || !envDefaultsValid}>
              {isPending ? "Saving..." : "Save Profile"}
            </Button>
          )}
        </div>
      </div>

      {/* Builtin notice */}
      {isBuiltin && (
        <div className="flex items-start gap-2.5 px-4 py-3 rounded-md bg-cyan-500/[0.06] border border-cyan-500/20 text-sm text-cyan-100/80">
          <Info className="w-4 h-4 mt-0.5 shrink-0 text-cyan-300" />
          <div>
            This is a builtin profile. Its fields are read-only here. To revert
            any local changes that drift from the built-in defaults, use{" "}
            <span className="font-semibold">Reset to Default</span>.
          </div>
        </div>
      )}

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
                disabled={isEditMode || isBuiltin}
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
                disabled={isBuiltin}
                placeholder="athanor-sandbox:latest"
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
                disabled={isBuiltin}
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
                disabled={isBuiltin}
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
                disabled={isBuiltin}
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
                disabled={isBuiltin}
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
                disabled={isBuiltin}
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
                  disabled={isBuiltin}
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
                  disabled={isBuiltin}
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
                disabled={isBuiltin}
              />
            </div>
            <p className="text-xs text-white/30 mt-1">e.g. no-new-privileges, seccomp=profile.json</p>
          </div>
        </CardContent>
      </Card>

      {/* Section 4: QA & Networking */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">QA &amp; Networking</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={form.description}
              onChange={(e) => setField("description", e.target.value)}
              disabled={isBuiltin}
              placeholder="Short summary shown on the sandboxes list"
              className="mt-1"
            />
          </div>

          <div className="flex items-start gap-3">
            <input
              id="dind_enabled"
              type="checkbox"
              checked={form.dind_enabled}
              onChange={(e) => setField("dind_enabled", e.target.checked)}
              disabled={isBuiltin}
              className="mt-1 h-4 w-4 rounded border-white/20 bg-white/5 text-cyan-500 focus:ring-cyan-500/50 disabled:opacity-50"
            />
            <div>
              <Label htmlFor="dind_enabled" className="cursor-pointer">DinD enabled</Label>
              <p className="text-xs text-white/30 mt-0.5">
                Run a Docker-in-Docker daemon inside the sandbox so QA jobs can build and run containers.
              </p>
            </div>
          </div>

          <div>
            <Label>Extra Networks</Label>
            <div className="mt-1">
              <TagInput
                value={form.extra_networks}
                onChange={(tags) => setField("extra_networks", tags)}
                placeholder="Add network..."
                disabled={isBuiltin}
              />
            </div>
            <p className="text-xs text-white/30 mt-1">
              Additional Docker networks the sandbox is attached to (besides the default sandbox-restricted network).
            </p>
          </div>

          <div>
            <Label htmlFor="idle_timeout_seconds">Idle Timeout (seconds)</Label>
            <Input
              id="idle_timeout_seconds"
              type="number"
              min={1}
              value={form.idle_timeout_seconds}
              onChange={(e) => {
                const n = parseInt(e.target.value, 10);
                if (!isNaN(n) && n > 0) setField("idle_timeout_seconds", n);
              }}
              disabled={isBuiltin}
              className="mt-1"
            />
            <p className="text-xs text-white/30 mt-1">
              Reap the sandbox after this many seconds with no agent activity.
            </p>
          </div>

          <div>
            <Label htmlFor="env_defaults_json">Env Defaults (JSON)</Label>
            <textarea
              id="env_defaults_json"
              value={envDefaultsText}
              onChange={(e) => handleEnvDefaultsChange(e.target.value)}
              disabled={isBuiltin}
              rows={5}
              className="mt-1 w-full font-mono text-xs bg-white/5 border border-white/10 rounded px-2 py-1.5 disabled:opacity-50"
              placeholder='{"LANG": "C.UTF-8"}'
            />
            <p className="text-xs text-white/30 mt-1">
              Non-secret env vars set by the profile. Stored as a JSON object of string values.
            </p>
            {!envDefaultsValid && (
              <p className="text-xs text-red-400 mt-1">
                Invalid JSON — must be a flat object of string values, e.g. {`{"KEY": "value"}`}.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Section 5: Timeouts */}
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
                disabled={isBuiltin}
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
                disabled={isBuiltin}
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

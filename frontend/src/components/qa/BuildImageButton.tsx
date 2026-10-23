import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Hammer } from "lucide-react";
import { toast } from "sonner";
import { buildQaImage } from "@/api/qaDashboard";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Switch } from "@/components/ui/Switch";

function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 422 || response?.status === 403 || response?.status === 409) {
    return formatErrorDetail(response.data?.detail) ?? "The build was refused.";
  }
  return null;
}

/**
 * Bake the repository's pre-built QA image (spec 3.7, "QA image build"): a
 * button that asks for the branch and whether to rebuild an image that is
 * already current, then calls POST /qa/images/build and waits for it. The
 * route answers built or skipped and no image tag, so the toast says which.
 */
export function BuildImageButton({ repo }: { repo: string }) {
  const [open, setOpen] = useState(false);
  const [branch, setBranch] = useState("main");
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();

  const build = useMutation({
    mutationFn: () => buildQaImage(repo, branch.trim(), force),
    onSuccess: (result) => {
      const where = `${result.repo} (${result.branch})`;
      if (result.status === "built") toast.success(`QA image built for ${where}`);
      else toast.info(`QA image already current for ${where}`);
      void qc.invalidateQueries({ queryKey: ["qa-dashboard", "cache-analytics", repo] });
      setOpen(false);
    },
    onError: (e) => {
      const shown = refusal(e);
      if (shown) setError(shown);
      else toastApiError(e, "QA image build failed");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!branch.trim()) {
      setError("Name the branch to build from.");
      return;
    }
    setError(null);
    build.mutate();
  };

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
        data-testid="build-qa-image"
      >
        <Hammer className="mr-2 h-4 w-4" />
        Build QA image
      </Button>
      {open && (
        <Dialog onClose={() => !build.isPending && setOpen(false)} title="Build QA image" dismissOnBackdrop={false} className="max-w-md">
          <form onSubmit={submit} className="space-y-4 p-5">
            <p className="text-sm text-fg-secondary">
              Clones {repo}, installs its dependencies and commits the result as the image QA runs start from. A fresh build can take minutes; this page waits for it.
            </p>
            <div className="space-y-1">
              <Label htmlFor="qa-image-branch">Branch</Label>
              <Input
                id="qa-image-branch"
                value={branch}
                onChange={(e) => {
                  setError(null);
                  setBranch(e.target.value);
                }}
                disabled={build.isPending}
                data-autofocus
              />
            </div>
            <div className="flex items-center gap-3">
              <Switch id="qa-image-force" checked={force} onCheckedChange={setForce} disabled={build.isPending} />
              <Label htmlFor="qa-image-force">Rebuild even if nothing changed</Label>
            </div>
            {error && (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setOpen(false)} disabled={build.isPending}>
                Cancel
              </Button>
              <Button type="submit" disabled={build.isPending}>
                {build.isPending ? "Building… (can take minutes)" : "Build"}
              </Button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  );
}

import { useState } from "react";
import { toast } from "sonner";
import type { AreasSaveResult } from "@/api/areas";
import { isConflict } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { TextDiff } from "@/components/documents/TextDiff";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { usePreviewRepoAreas, useRereadRepoAreas, useSaveRepoAreas } from "@/hooks/useRepoAreas";
import { documentProblems, type DocumentProblem } from "@/lib/settingsDocument";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { AreaChangeList } from "./AreaChangeList";

/** What an empty repository starts from: the document's shape, every key explained. */
export const STARTER = `# The parts of this repository. The first area whose paths match a file has it.
# name: what embry0 calls the part; paths: globs from the root (** any depth, a trailing / all below);
# logs: strings in its log lines; owners: logins asked first about its work.
areas:
  - name: Web
    paths: ["web/"]
`;

const CONFLICT = "Someone changed these areas after you started. They were read in again; your text is kept: preview it again.";

function Problems({ problems }: { problems: DocumentProblem[] }) {
  return (
    <div
      role="alert"
      data-testid="areas-problems"
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <p className="font-semibold">The areas were refused. Fix these, then preview again:</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {problems.map((problem, index) => (
          <li key={index} data-testid="areas-problem">
            {problem.key && <code className="font-mono">{problem.key}</code>}
            {problem.key ? `: ${problem.msg}` : problem.msg}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * A repository's areas as YAML text (spec 2026-11-10). A change is previewed first (what it does area by area,
 * the owners it adds or removes first, and the text diff), then saved; both name the generation the edit started
 * from, so a change over someone else's newer one is refused instead of overwriting it. Every problem of a
 * refused document is listed by its key. `initialText` seeds the editor (a map's document, to change before
 * saving); `onDone` closes it.
 */
export function AreasEditor({
  repo,
  stored,
  generation,
  initialText,
  onDone,
}: {
  repo: string;
  stored: string;
  generation: number;
  initialText?: string;
  onDone: () => void;
}) {
  const preview = usePreviewRepoAreas(repo);
  const save = useSaveRepoAreas(repo);
  const reread = useRereadRepoAreas(repo);
  const [text, setText] = useState(initialText ?? (stored || STARTER));
  const [base, setBase] = useState(generation);
  const [previewed, setPreviewed] = useState<{ text: string; result: AreasSaveResult } | null>(null);
  const [problems, setProblems] = useState<DocumentProblem[] | null>(null);
  const [conflict, setConflict] = useState(false);
  const busy = preview.isPending || save.isPending;
  const current = previewed?.text === text ? previewed.result : null;

  function refused(error: unknown, what: string) {
    if (isConflict(error)) {
      // Someone saved meanwhile: read their version in, keep this text, and preview it against theirs next.
      setConflict(true);
      setPreviewed(null);
      reread().then(
        (fresh) => setBase(fresh.generation),
        (failed: unknown) => toastApiError(failed, "Could not read the areas again"),
      );
      return;
    }
    const found = documentProblems(error);
    if (found) setProblems(found);
    else toastApiError(error, what);
  }

  function onPreview() {
    setProblems(null);
    setConflict(false);
    preview.mutate(
      { yamlText: text, generation: base },
      {
        onSuccess: (result) => setPreviewed({ text, result }),
        onError: (error) => refused(error, "Could not preview the areas"),
      },
    );
  }

  function onSave() {
    setProblems(null);
    setConflict(false);
    save.mutate(
      { yamlText: text, generation: base },
      {
        onSuccess: (result) => {
          toast.success(result.revision_id === null ? "Nothing changed." : "Areas saved. History can undo it.");
          onDone();
        },
        onError: (error) => refused(error, "Could not save the areas"),
      },
    );
  }

  return (
    <div className="space-y-3" data-testid="areas-editor">
      <Textarea
        aria-label="Areas document"
        className="min-h-64 font-mono text-xs"
        spellCheck={false}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      {conflict && (
        <p role="alert" className="text-sm text-warning" data-testid="areas-conflict">
          {CONFLICT}
        </p>
      )}
      {problems && <Problems problems={problems} />}
      {current && (
        <div className="space-y-2 rounded-lg border border-border p-3" data-testid="areas-preview">
          {current.would_change === false ? (
            <p className="text-sm text-fg-muted">Nothing would change: this is the text already stored.</p>
          ) : (
            <AreaChangeList changes={current.changes} />
          )}
          {current.text_diff && <TextDiff lines={parseUnifiedDiff(current.text_diff)} label="Text changes" />}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" disabled={busy} onClick={onPreview}>
          Preview
        </Button>
        <Button size="sm" disabled={busy || !current} onClick={onSave}>
          Save
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={onDone}>
          Cancel
        </Button>
      </div>
      {!current && <p className="text-xs text-fg-muted">Preview the change to save it.</p>}
    </div>
  );
}

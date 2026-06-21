import { useEffect, useState } from "react";
import { Camera, MessageSquarePlus, X } from "lucide-react";
import {
  submitFeedback,
  type FeedbackCategory,
  type FeedbackLevel,
} from "@/api/agent";
import { captureScreen } from "@/lib/capture-screen";
import { cn } from "@/lib/utils";

// Phase-5 global FAB. Mounted once at AppLayout. Self-contained — opens a
// modal that posts to the companion agent's `/feedback` intake.

const CATEGORIES: ReadonlyArray<{ value: FeedbackCategory; label: string }> = [
  { value: "bug", label: "Bug report" },
  { value: "feature", label: "Feature request" },
];

const LEVELS: ReadonlyArray<{ value: FeedbackLevel; label: string }> = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

export function FeedbackFab() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        data-testid="feedback-fab"
        aria-label="Send feedback"
        title="Send feedback"
        onClick={() => setOpen(true)}
        className={cn(
          "fixed bottom-6 right-6 z-40 flex h-12 w-12 items-center justify-center rounded-full",
          "bg-primary text-primary-foreground shadow-lg shadow-primary/20",
          "transition-colors hover:bg-primary/90",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        )}
      >
        <MessageSquarePlus className="h-5 w-5" />
      </button>
      {open && <FeedbackModal onClose={() => setOpen(false)} />}
    </>
  );
}

function FeedbackModal({ onClose }: { onClose: () => void }) {
  const [category, setCategory] = useState<FeedbackCategory>("bug");
  const [severity, setSeverity] = useState<FeedbackLevel>("medium");
  const [urgency, setUrgency] = useState<FeedbackLevel>("medium");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [screenshot, setScreenshot] = useState<Blob | null>(null);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const onCapture = async () => {
    setCaptureError(null);
    try {
      const blob = await captureScreen();
      setScreenshot(blob);
    } catch (err) {
      setCaptureError(err instanceof Error ? err.message : "capture failed");
    }
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitFeedback({
        category,
        severity,
        urgency,
        title: title.trim(),
        body: body.trim(),
        ...(screenshot ? { screenshot } : {}),
      });
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "submission failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="feedback-modal-title"
      data-testid="feedback-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <form
        onSubmit={onSubmit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-lg border border-white/10 bg-background p-5 shadow-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2
            id="feedback-modal-title"
            className="text-sm font-medium uppercase tracking-[0.18em] text-white/80"
          >
            Send feedback
          </h2>
          <button
            type="button"
            data-testid="feedback-modal-close"
            aria-label="Close feedback"
            onClick={onClose}
            className="rounded-md p-1 text-white/60 hover:bg-white/10 hover:text-white"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Field label="Category" id="feedback-category">
            <Select
              id="feedback-category"
              value={category}
              onChange={(e) => setCategory(e.target.value as FeedbackCategory)}
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Severity" id="feedback-severity">
            <Select
              id="feedback-severity"
              value={severity}
              onChange={(e) => setSeverity(e.target.value as FeedbackLevel)}
            >
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Urgency" id="feedback-urgency">
            <Select
              id="feedback-urgency"
              value={urgency}
              onChange={(e) => setUrgency(e.target.value as FeedbackLevel)}
            >
              {LEVELS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <div className="mt-3">
          <Field label="Title" id="feedback-title">
            <input
              id="feedback-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className={inputClass}
              placeholder="Short summary"
            />
          </Field>
        </div>

        <div className="mt-3">
          <Field label="Details" id="feedback-body">
            <textarea
              id="feedback-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={5}
              className={cn(inputClass, "resize-y")}
              placeholder="What happened, what did you expect?"
            />
          </Field>
        </div>

        <div className="mt-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-white/60">
            <button
              type="button"
              data-testid="feedback-capture"
              onClick={onCapture}
              className="inline-flex items-center gap-1.5 rounded-md border border-white/10 px-2.5 py-1.5 text-white/80 hover:bg-white/5"
            >
              <Camera className="h-3.5 w-3.5" />
              {screenshot ? "Recapture" : "Capture screen"}
            </button>
            {screenshot && (
              <span
                data-testid="feedback-screenshot-attached"
                className="text-success"
              >
                screenshot attached
              </span>
            )}
            {captureError && (
              <span data-testid="feedback-capture-error" className="text-destructive">
                {captureError}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-3 py-1.5 text-sm text-white/70 hover:bg-white/5"
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="feedback-submit"
              disabled={submitting}
              className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {submitting ? "Sending…" : "Send"}
            </button>
          </div>
        </div>

        {submitError && (
          <p
            data-testid="feedback-error"
            className="mt-3 text-sm text-destructive"
            role="alert"
          >
            {submitError}
          </p>
        )}
      </form>
    </div>
  );
}

const inputClass = cn(
  "w-full rounded-md border border-white/10 bg-black/30 px-2.5 py-1.5 text-sm text-white",
  "placeholder:text-white/30",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

function Field({
  label,
  id,
  children,
}: {
  label: string;
  id: string;
  children: React.ReactNode;
}) {
  return (
    <label htmlFor={id} className="block text-xs font-medium text-white/60">
      <span className="mb-1 block uppercase tracking-[0.16em]">{label}</span>
      {children}
    </label>
  );
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn(inputClass, props.className)} />;
}

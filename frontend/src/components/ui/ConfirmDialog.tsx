import type { ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";

const noop = () => {};

export interface ConfirmDialogProps {
  /** A question, which is also the dialog's name: "Remove web-1?". */
  title: string;
  /** What the action does and what it costs, in a sentence or two. */
  children: ReactNode;
  confirmLabel: string;
  /** A destructive action's button is red; the default is the page's primary. */
  destructive?: boolean;
  /** While the action runs both buttons are disabled and the confirm says so. */
  pending?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * The one "are you sure" (spec 3.7): a Dialog that must be answered, so the
 * backdrop does not dismiss it; Escape and Cancel do, except while the action
 * runs, when nothing closes it. Focus lands on Cancel, so a stray Enter never
 * confirms. It opens from a page, a row or a tab, never from inside another
 * Dialog.
 */
export function ConfirmDialog({ title, children, confirmLabel, destructive, pending, onConfirm, onClose }: ConfirmDialogProps) {
  return (
    <Dialog onClose={pending ? noop : onClose} title={title} dismissOnBackdrop={false} className="max-w-md">
      <div className="space-y-4 p-5">
        <div className="text-sm text-fg-secondary">{children}</div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending} data-autofocus>
            Cancel
          </Button>
          <Button type="button" variant={destructive ? "destructive" : "default"} onClick={onConfirm} disabled={pending}>
            {pending ? "Working…" : confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

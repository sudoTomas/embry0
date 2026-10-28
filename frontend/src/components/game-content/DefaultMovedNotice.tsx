import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useDefaultDiff } from "@/hooks/useProgressionPack";
import { TextDiff } from "@/components/documents/TextDiff";
import { parseUnifiedDiff } from "@/lib/textDiff";

/**
 * The shipped default changed since the pack in effect was taken from it.
 * A customised pack is a whole document, so a new release's default does
 * not reach it: this shows what moved, for the admin to carry over by hand.
 */
export function DefaultMovedNotice() {
  const [open, setOpen] = useState(false);
  const diff = useDefaultDiff(open);
  return (
    <div
      role="status"
      data-testid="default-moved"
      className="space-y-2 rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning"
    >
      <p>
        The shipped default changed since this pack was taken from it. Your pack is a whole document, so the change
        does not reach it. Read what moved and carry over what you want, then save the draft with{" "}
        <q>I have taken in the new default</q> ticked, and this notice goes away once it is active.
      </p>
      <Button
        variant="outline"
        size="xs"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        data-testid="default-moved-toggle"
      >
        {open ? "Hide what moved" : "Show what moved"}
      </Button>
      {open &&
        (diff.isError ? (
          <p className="text-fg-secondary">Could not load what moved.</p>
        ) : !diff.data ? (
          <Skeleton className="h-16 w-full" />
        ) : diff.data.text_diff === null ? (
          <p className="text-fg-secondary" data-testid="default-moved-unknown">
            The default your pack was taken from was not recorded, so what moved cannot be shown. Compare your pack
            with the shipped default (export it from the API) instead.
          </p>
        ) : (
          <TextDiff lines={parseUnifiedDiff(diff.data.text_diff)} label="How the shipped default changed" />
        ))}
    </div>
  );
}

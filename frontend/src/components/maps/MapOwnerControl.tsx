import { useState } from "react";
import { toast } from "sonner";
import type { MapOwner } from "@/api/maps";
import { toastApiError } from "@/api/errorToast";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useSetMapOwner } from "@/hooks/useMaps";

interface MapOwnerControlProps {
  slug: string;
  /** The map's owner as the page names it: `""` for a map of no owner. */
  owner: MapOwner;
  /** Called with the new owner (`""`: none) once the map moved, so the page can follow it. */
  onMoved: (owner: string) => void;
}

/** Admin (#180): give a map an owner, or another one. Its marks, sends and leads go with it. */
export function MapOwnerControl({ slug, owner, onMoved }: MapOwnerControlProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(owner ?? "");
  const move = useSetMapOwner(slug, owner);
  if (!editing) {
    return (
      <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
        Set owner
      </Button>
    );
  }
  const save = () =>
    move.mutate(value.trim() || null, {
      onSuccess: (r) => {
        setEditing(false);
        toast.success(r.owner ? `The map belongs to ${r.owner}` : "The map belongs to no owner: admins only");
        onMoved(r.owner ?? "");
      },
      onError: (e: Error) => toastApiError(e, "The owner was not changed"),
    });
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Input
        className="w-40"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Owner (empty: none)"
        aria-label="Owner"
      />
      <Button size="sm" type="submit" disabled={move.isPending}>
        Save
      </Button>
      <Button size="sm" variant="ghost" type="button" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </form>
  );
}

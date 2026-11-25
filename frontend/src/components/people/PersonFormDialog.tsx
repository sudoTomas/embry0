import { useState, type FormEvent } from "react";
import { X } from "lucide-react";
import { toastApiError } from "@/api/errorToast";
import {
  IDENTITY_KINDS,
  type DirectoryPerson,
  type Identity,
  type IdentityKind,
  type PersonWrite,
  type SuggestedUser,
} from "@/api/people";
import { useCreatePerson, useUpdatePerson } from "@/hooks/usePeopleDirectory";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { IDENTITY_LABELS, refusal } from "@/lib/people";

interface Props {
  /** The person to edit; absent, the form adds one. */
  person?: DirectoryPerson;
  /** A new person's first values, such as a suggestion's. */
  initial?: Partial<PersonWrite>;
  /** The users who sign in with no person yet: whom a person may be linked to. */
  users: SuggestedUser[];
  onClose: () => void;
}

const blank = (value: string) => (value.trim() ? value.trim() : null);

/**
 * Add or edit a person (#179): who they are, the sign-in they are, when they work and the identities that are
 * theirs. A change names the version it was opened on, so one made in between is refused, never overwritten; the
 * server's refusals (a value someone else holds, a field it cannot read) are shown under the form, which stays
 * open with what was typed. Hours, weekdays and time zone left empty read the organization's.
 */
export function PersonFormDialog({ person, initial, users, onClose }: Props) {
  const from = person ?? initial;
  const [name, setName] = useState(from?.name ?? "");
  const [email, setEmail] = useState(from?.email ?? "");
  const [userId, setUserId] = useState(from?.user_id ?? "");
  const [timezone, setTimezone] = useState(from?.timezone ?? "");
  const [hours, setHours] = useState(from?.hours ?? "");
  const [weekdays, setWeekdays] = useState(from?.weekdays ?? "");
  const [disabled, setDisabled] = useState(from?.disabled ?? false);
  const [identities, setIdentities] = useState<Identity[]>(from?.identities ?? []);
  const [error, setError] = useState<string | null>(null);
  const create = useCreatePerson();
  const update = useUpdatePerson();
  const pending = create.isPending || update.isPending;
  // The sign-in a person has now stays offered, as does each user with no person.
  const signIns = users.filter((u) => u.user_id !== person?.user_id);

  const setIdentity = (i: number, change: Partial<Identity>) =>
    setIdentities(identities.map((identity, j) => (j === i ? { ...identity, ...change } : identity)));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError("Name the person.");
      return;
    }
    if (identities.some((identity) => !identity.value.trim())) {
      setError("Give each identity a value, or take it out.");
      return;
    }
    setError(null);
    const body: PersonWrite = {
      name: name.trim(),
      email: blank(email),
      user_id: userId || null,
      timezone: blank(timezone),
      hours: blank(hours),
      weekdays: blank(weekdays),
      disabled,
      identities: identities.map((identity) => ({ kind: identity.kind, value: identity.value.trim() })),
    };
    const opts = {
      onSuccess: onClose,
      onError: (err: unknown) => {
        const shown = refusal(err);
        if (shown) setError(shown);
        else toastApiError(err, person ? "The person was not saved" : "The person was not added");
      },
    };
    if (person) update.mutate({ person, body }, opts);
    else create.mutate(body, opts);
  };

  return (
    <Dialog
      onClose={() => !pending && onClose()}
      title={person ? `Edit ${person.name}` : "Add person"}
      dismissOnBackdrop={false}
      className="max-w-xl"
    >
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="person-name">Name</Label>
            <Input id="person-name" value={name} onChange={(e) => setName(e.target.value)} disabled={pending} data-autofocus />
          </div>
          <div className="space-y-1">
            <Label htmlFor="person-email">Email</Label>
            <Input id="person-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={pending} />
            <p className="text-xs text-fg-muted">What they are asked by; without one, by their forge login.</p>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="person-sign-in">Signs in as</Label>
          <Select id="person-sign-in" value={userId} onChange={(e) => setUserId(e.target.value)} disabled={pending}>
            <option value="">Does not sign in</option>
            {person?.user_id && <option value={person.user_id}>The sign-in they have now</option>}
            {signIns.map((u) => (
              <option key={u.user_id} value={u.user_id}>
                {u.name ? `${u.name} (${u.email})` : u.email}
              </option>
            ))}
          </Select>
          <p className="text-xs text-fg-muted">A user who signs in and is no person yet.</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor="person-timezone">Time zone</Label>
            <Input id="person-timezone" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="Europe/London" disabled={pending} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="person-hours">Hours</Label>
            <Input id="person-hours" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="09-17" disabled={pending} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="person-weekdays">Weekdays</Label>
            <Input id="person-weekdays" value={weekdays} onChange={(e) => setWeekdays(e.target.value)} placeholder="mon-fri" disabled={pending} />
          </div>
        </div>
        <p className="text-xs text-fg-muted">Left empty, each reads their organization&apos;s working hours.</p>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Identities</legend>
          {identities.map((identity, i) => (
            <div key={i} className="flex items-center gap-2" data-testid="identity-row">
              <Select
                aria-label={`Identity ${i + 1} kind`}
                value={identity.kind}
                onChange={(e) => setIdentity(i, { kind: e.target.value as IdentityKind })}
                disabled={pending}
                className="w-36"
              >
                {IDENTITY_KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {IDENTITY_LABELS[kind]}
                  </option>
                ))}
              </Select>
              <Input
                aria-label={`Identity ${i + 1} value`}
                value={identity.value}
                onChange={(e) => setIdentity(i, { value: e.target.value })}
                placeholder={identity.kind === "github" || identity.kind === "gitlab" ? "login" : "their id in the chat"}
                disabled={pending}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`Take out identity ${i + 1}`}
                onClick={() => setIdentities(identities.filter((_, j) => j !== i))}
                disabled={pending}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setIdentities([...identities, { kind: "github", value: "" }])}
            disabled={pending}
          >
            Add identity
          </Button>
          <p className="text-xs text-fg-muted">
            A login on a forge names them as an owner; a chat handle is where an assistant reaches them.
          </p>
        </fieldset>
        {person && (
          <div className="flex items-center gap-2">
            <Switch id="person-disabled" checked={disabled} onCheckedChange={setDisabled} label="Disabled" disabled={pending} />
            <span className="text-sm text-fg-secondary" aria-hidden="true">
              Disabled: neither asked nor an owner, kept for the record
            </span>
          </div>
        )}
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button type="submit" disabled={pending}>
            {pending ? "Saving…" : person ? "Save" : "Add person"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

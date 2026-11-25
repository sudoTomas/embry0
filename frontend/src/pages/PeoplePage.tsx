import { useState } from "react";
import { Contact } from "lucide-react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { FORGE_KINDS, type DirectoryPerson, type IdentityKind, type PersonWrite } from "@/api/people";
import { usePeopleDirectory, usePeopleSuggestions, useRemovePerson } from "@/hooks/usePeopleDirectory";
import { AdminButton } from "@/components/auth/AdminButton";
import { PersonFormDialog } from "@/components/people/PersonFormDialog";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { Menu } from "@/components/ui/Menu";
import { Switch } from "@/components/ui/Switch";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { IDENTITY_LABELS, workingHours } from "@/lib/people";
import { useIsAdmin } from "@/stores/authStore";

type Open =
  | { kind: "add"; initial?: Partial<PersonWrite> }
  | { kind: "edit"; person: DirectoryPerson }
  | { kind: "remove"; person: DirectoryPerson }
  | null;

const isForge = (forge: string): forge is IdentityKind => (FORGE_KINDS as readonly string[]).includes(forge);

/**
 * People (#179): who embry0 asks and names as an owner, whether or not they sign in. Operators and admins read;
 * adding, editing and removing are an admin's and are offered disabled below that. Below the people, who could be
 * added: the users who sign in and are no person yet, and the owner logins nobody holds (a user standing in by their
 * GitHub login holds theirs), each with an Add that starts the form from what embry0 knows of them. embry0 adds nobody
 * by itself.
 */
export function PeoplePage() {
  const [includeDisabled, setIncludeDisabled] = useState(false);
  const [open, setOpen] = useState<Open>(null);
  const isAdmin = useIsAdmin();
  const list = usePeopleDirectory(includeDisabled);
  const suggestions = usePeopleSuggestions();
  const remove = useRemovePerson();
  const users = suggestions.data?.users ?? [];
  const owners = suggestions.data?.owners ?? [];

  const runRemove = (person: DirectoryPerson) =>
    remove.mutate(person.id, {
      onSuccess: () => {
        toast.success(`Removed ${person.name}`);
        setOpen(null);
      },
      onError: (e) => {
        setOpen(null);
        toastApiError(e, `Could not remove ${person.name}`);
      },
    });

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load people" onRetry={() => void list.refetch()} />;
  } else if (list.isPending || !list.data) {
    body = <TableSkeleton />;
  } else if (list.data.length === 0) {
    body = (
      <EmptyState
        icon={Contact}
        title="No people yet"
        description="Add the people embry0 asks about decisions and names as owners, or start from the suggestions below."
      />
    );
  } else {
    body = (
      // On a phone the identities and the hours step aside.
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col" className="hidden sm:table-cell">Identities</th>
              <th scope="col" className="hidden sm:table-cell">Working hours</th>
              <th scope="col">Signs in</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((person) => (
              <tr key={person.id} data-testid="person-row">
                {/* A long name or email wraps anywhere, so on a phone the row's Actions stay on screen. */}
                <td className="[overflow-wrap:anywhere]" data-testid="person-name">
                  <span className="font-medium">{person.name}</span>
                  {person.disabled && (
                    <Badge tone="neutral" className="ml-2">
                      Disabled
                    </Badge>
                  )}
                  <span className="block text-xs text-fg-muted">{person.email ?? "No email"}</span>
                </td>
                <td className="hidden sm:table-cell">
                  <span className="inline-flex flex-wrap gap-1">
                    {person.identities.map((identity) => (
                      <Badge key={`${identity.kind}:${identity.value}`} tone="neutral">
                        {IDENTITY_LABELS[identity.kind]} {identity.value}
                      </Badge>
                    ))}
                  </span>
                </td>
                <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{workingHours(person)}</td>
                <td className="whitespace-nowrap text-fg-secondary">{person.user_id ? "Yes" : "No"}</td>
                <td className="w-px">
                  <Menu
                    label={`Actions for ${person.name}`}
                    items={[
                      { label: "Edit", onSelect: () => setOpen({ kind: "edit", person }), disabled: !isAdmin },
                      {
                        label: "Remove",
                        onSelect: () => setOpen({ kind: "remove", person }),
                        destructive: true,
                        disabled: !isAdmin,
                      },
                    ]}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-page text-xl font-bold">People</h1>
          <p className="text-sm text-muted-foreground">
            Who embry0 asks about decisions and names as owners, whether or not they sign in.
          </p>
        </div>
        <AdminButton type="button" onClick={() => setOpen({ kind: "add" })}>
          Add person
        </AdminButton>
      </div>
      <div className="flex items-center gap-2">
        <Switch id="people-show-disabled" checked={includeDisabled} onCheckedChange={setIncludeDisabled} label="Show disabled" />
        <span className="text-sm text-fg-secondary" aria-hidden="true">
          Show disabled
        </span>
      </div>
      {body}
      <section className="space-y-3" aria-labelledby="people-suggestions">
        <div>
          <h2 id="people-suggestions" className="text-base font-semibold">
            Not people yet
          </h2>
          <p className="text-sm text-muted-foreground">
            Who embry0 meets but does not know as a person. An owner login is still asked as itself until someone adds
            them.
          </p>
        </div>
        {suggestions.isError ? (
          <PageError message="Failed to load the suggestions" onRetry={() => void suggestions.refetch()} />
        ) : users.length === 0 && owners.length === 0 ? (
          <p className="text-sm text-fg-secondary">{suggestions.isPending ? "Reading…" : "Everyone embry0 meets is a person."}</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {users.map((user) => (
              <li key={user.user_id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2" data-testid="suggested-user">
                <span>
                  <span className="font-medium">{user.name || user.email}</span>
                  <span className="block text-xs text-fg-muted">
                    Signs in as {user.email}
                    {user.github_login ? ` · GitHub ${user.github_login}` : ""}
                  </span>
                </span>
                <AdminButton
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={`Add ${user.name || user.email}`}
                  onClick={() =>
                    setOpen({
                      kind: "add",
                      initial: {
                        name: user.name || user.email,
                        email: user.email,
                        user_id: user.user_id,
                        identities: user.github_login ? [{ kind: "github", value: user.github_login }] : [],
                      },
                    })
                  }
                >
                  Add
                </AdminButton>
              </li>
            ))}
            {owners.map((owner) => (
              <li
                key={`${owner.forge}:${owner.login}`}
                className="flex flex-wrap items-center justify-between gap-2 px-4 py-2"
                data-testid="suggested-owner"
              >
                <span>
                  <span className="font-medium">
                    {isForge(owner.forge) ? IDENTITY_LABELS[owner.forge] : owner.forge} {owner.login}
                  </span>
                  <span className="block text-xs text-fg-muted">Owner of {owner.repos.join(", ")}</span>
                </span>
                <AdminButton
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={`Add ${owner.forge} ${owner.login}`}
                  onClick={() =>
                    setOpen({
                      kind: "add",
                      initial: {
                        name: owner.login,
                        identities: isForge(owner.forge) ? [{ kind: owner.forge, value: owner.login }] : [],
                      },
                    })
                  }
                >
                  Add
                </AdminButton>
              </li>
            ))}
          </ul>
        )}
      </section>
      {(open?.kind === "add" || open?.kind === "edit") && (
        <PersonFormDialog
          person={open.kind === "edit" ? open.person : undefined}
          initial={open.kind === "add" ? open.initial : undefined}
          users={users}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "remove" && (
        <ConfirmDialog
          title={`Remove ${open.person.name}?`}
          confirmLabel="Remove"
          destructive
          pending={remove.isPending}
          onConfirm={() => runRemove(open.person)}
          onClose={() => setOpen(null)}
        >
          Their identities go with them, and the owner logins they held are asked as themselves again. A sign-in they
          were linked to keeps working. To keep them on record, disable them instead.
        </ConfirmDialog>
      )}
    </div>
  );
}

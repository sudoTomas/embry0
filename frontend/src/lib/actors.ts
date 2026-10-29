/**
 * Who did something, as the API answers it. It shows an admin every actor and
 * anyone else only their own, with null for everyone else's, the way `GET /audit`
 * shows actors. A view names the actor when it has one and says "Someone else"
 * when it does not.
 */
export const SOMEONE_ELSE = "Someone else";

/** The actor to show on its own (a list's "who" column): the name, or "Someone else". */
export function actorLabel(actor: string | null | undefined): string {
  return actor || SOMEONE_ELSE;
}

/** The actor inside a sentence ("Decided by …"): the name, or "someone else". */
export function actorInText(actor: string | null | undefined): string {
  return actor || SOMEONE_ELSE.toLowerCase();
}

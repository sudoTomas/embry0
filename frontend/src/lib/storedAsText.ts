/**
 * A stored value too deeply nested for the API to send as it is (#431): the API sends its JSON text instead,
 * and the dashboard shows that text read-only rather than reading it as the object it expected.
 */

/** What the dashboard says of such a value. */
export const STORED_AS_TEXT_NOTE = "Too deeply nested to show; stored as text.";

/** Whether ``value`` arrived as the text the API sends for a value too deep to serve, not the object it is. */
export function storedAsText(value: unknown): value is string {
  return typeof value === "string";
}

import { formatErrorDetail } from "@/api/errorDetail";
import type { DirectoryPerson, IdentityKind } from "@/api/people";

export const IDENTITY_LABELS: Record<IdentityKind, string> = {
  github: "GitHub",
  gitlab: "GitLab",
  telegram: "Telegram",
  slack: "Slack",
  teams: "Teams",
  discord: "Discord",
};

/** When a person works, as far as they set it; what they leave out reads their organization's. */
export function workingHours(person: Pick<DirectoryPerson, "timezone" | "hours" | "weekdays">): string {
  const set = [person.hours, person.weekdays, person.timezone].filter((part): part is string => Boolean(part));
  return set.length ? set.join(" · ") : "Their organization's";
}

/** What the server said, shown under the form; null for a failure the toast says instead. */
export function refusal(err: unknown): string | null {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  const detail = formatErrorDetail(response?.data?.detail);
  if (response?.status === 403) return "Admin scope required.";
  if (response?.status === 409 && detail?.includes("changed since")) {
    return "Someone changed this person since you opened it. It has been read again: close this and edit it again.";
  }
  if (response?.status === 409 || response?.status === 422) return detail ?? "The person was not saved.";
  if (response?.status === 404) return "This person was removed meanwhile.";
  return null;
}

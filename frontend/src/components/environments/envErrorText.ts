import { detailMessages } from "@/api/errorDetail";

/**
 * The reason a save failed, from the API's error `detail`. Reserved keys are
 * refused by the API alone (embry0/execution/auth_provider.py): a client-side
 * copy of that list drifted (#543), and the 422 already names the key and why.
 */
export function envErrorText(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  const parts = detailMessages(detail);
  if (parts.length > 0) return parts.join(" ");
  if (error instanceof Error && error.message) return error.message;
  return "Request failed";
}

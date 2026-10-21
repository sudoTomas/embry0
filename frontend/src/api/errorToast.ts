import { toast } from "sonner";
import { apiErrorMessage, isReported } from "./errorDetail";

/**
 * Toast why a write failed — "<context>: <the API's reason>" — unless the api
 * client already showed it (a 422, a 5xx, a network failure or a 401), so the
 * operator reads each failure once, with its reason.
 */
export function toastApiError(error: unknown, context?: string): void {
  if (isReported(error)) return;
  const reason = apiErrorMessage(error);
  toast.error(context ? `${context}: ${reason}` : reason);
}

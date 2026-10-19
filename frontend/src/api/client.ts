import axios, { AxiosError } from "axios";
import { toast } from "sonner";
import { getApiKey } from "@/lib/apiKey";
import { useAuthStore } from "@/stores/authStore";
import { formatErrorDetail, markReported } from "./errorDetail";

declare module "axios" {
  export interface AxiosRequestConfig {
    /**
     * Skip the global 422 "Validation error" toast for this request — the
     * caller renders the detail itself (e.g. the qa.yaml editor, where the
     * detail is a multi-line schema report that belongs next to the editor).
     */
    suppressValidationToast?: boolean;
    /**
     * Skip the global network-error and server-error (5xx) toasts for this
     * request: the caller shows the failure itself (the AuthGate's sign-in
     * check, whose card already says the server did not answer).
     */
    suppressErrorToast?: boolean;
  }
}

export const api = axios.create({
  baseURL: "/api/v1",
  timeout: 30_000,
  headers: {
    "X-Requested-With": "XMLHttpRequest",
  },
});

// The key is resolved per-request from localStorage (the only source since
// RAV-1058) so a key entered through the AuthGate takes effect without a reload.
api.interceptors.request.use((config) => {
  const apiKey = getApiKey();
  if (apiKey && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${apiKey}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: unknown }>) => {
    if (error.code === "ERR_NETWORK" || error.code === "ERR_CANCELED") {
      if (!error.config?.suppressErrorToast) {
        toast.error("Network error — cannot reach the server");
        markReported(error);
      }
      return Promise.reject(error);
    }

    const status = error.response?.status;
    const detail = formatErrorDetail(error.response?.data?.detail);

    if (status === 401) {
      // Invalid/revoked key: surface the AuthGate prompt instead of letting
      // every widget degrade to "Unavailable" silently. No toast — the
      // full-screen prompt is the feedback.
      useAuthStore.getState().requirePrompt();
      markReported(error);
    } else if (status === 422) {
      if (!error.config?.suppressValidationToast) {
        toast.error(`Validation error: ${detail ?? "invalid request"}`);
        markReported(error);
      }
    } else if (status === 404) {
      // Don't toast 404s — let components handle "not found" states
    } else if (status && status >= 500) {
      if (!error.config?.suppressErrorToast) {
        toast.error(`Server error (${status}): ${detail ?? "please try again"}`);
        markReported(error);
      }
    }
    // Any other refusal (403, 409, ...) is the caller's to explain, with
    // apiErrorMessage; an error marked here is not toasted again
    // (toastApiError).

    return Promise.reject(error);
  }
);

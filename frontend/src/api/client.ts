import axios, { AxiosError } from "axios";
import { toast } from "sonner";

export const api = axios.create({
  baseURL: "/api/v1",
  timeout: 30_000,
  headers: {
    "X-Requested-With": "XMLHttpRequest",
  },
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: string }>) => {
    if (error.code === "ERR_NETWORK" || error.code === "ERR_CANCELED") {
      toast.error("Network error — cannot reach the server");
      return Promise.reject(error);
    }

    const status = error.response?.status;
    const detail = error.response?.data?.detail;

    if (status === 422) {
      toast.error(`Validation error: ${detail ?? "invalid request"}`);
    } else if (status === 404) {
      // Don't toast 404s — let components handle "not found" states
    } else if (status && status >= 500) {
      toast.error(`Server error (${status}): ${detail ?? "please try again"}`);
    }

    return Promise.reject(error);
  }
);

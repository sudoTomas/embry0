import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { router } from "@/router";

// Don't retry on 429 (rate limit) — retrying compounds the problem and the
// dashboard's polling tick will pick up the data on the next cycle anyway.
// Other errors get one retry (short network blip absorption).
function shouldRetry(failureCount: number, error: unknown): boolean {
  const status = (error as { response?: { status?: number } } | undefined)?.response?.status;
  if (status === 429) return false;
  return failureCount < 1;
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster theme="dark" position="bottom-right" />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

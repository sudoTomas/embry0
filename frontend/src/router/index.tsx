import { createBrowserRouter, Navigate } from "react-router";
import { lazy, Suspense } from "react";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageError } from "../components/PageError";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";

const DashboardPage = lazy(() => import("../pages/DashboardPage").then(m => ({ default: m.DashboardPage })));
const JobsPage = lazy(() => import("../pages/JobsPage").then(m => ({ default: m.JobsPage })));
const JobDetailPage = lazy(() => import("../pages/JobDetailPage").then(m => ({ default: m.JobDetailPage })));
const JobLogsPage = lazy(() => import("../pages/JobLogsPage").then(m => ({ default: m.JobLogsPage })));
const PipelinesPage = lazy(() => import("../pages/PipelinesPage").then(m => ({ default: m.PipelinesPage })));
const SettingsPage = lazy(() => import("../pages/SettingsPage").then(m => ({ default: m.SettingsPage })));
const NotFoundPage = lazy(() => import("../pages/NotFoundPage").then(m => ({ default: m.NotFoundPage })));

const fallback = <div className="p-6 text-muted-foreground">Loading...</div>;

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    errorElement: <PageError message="Something went wrong" />,
    children: [
      { index: true, element: <ErrorBoundary><Suspense fallback={fallback}><DashboardPage /></Suspense></ErrorBoundary> },
      { path: "jobs", element: <ErrorBoundary><Suspense fallback={fallback}><JobsPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId", element: <ErrorBoundary><Suspense fallback={fallback}><JobDetailPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId/logs", element: <ErrorBoundary><Suspense fallback={fallback}><JobLogsPage /></Suspense></ErrorBoundary> },
      { path: "pipelines", element: <ErrorBoundary><Suspense fallback={fallback}><PipelinesPage /></Suspense></ErrorBoundary> },
      { path: "traces", element: <Navigate to="/jobs" replace /> },
      { path: "triage", element: <Navigate to="/settings" replace /> },
      { path: "issues", element: <Navigate to="/jobs" replace /> },
      { path: "environments", element: <Navigate to="/settings" replace /> },
      { path: "settings", element: <ErrorBoundary><Suspense fallback={fallback}><SettingsPage /></Suspense></ErrorBoundary> },
      { path: "*", element: <ErrorBoundary><Suspense fallback={fallback}><NotFoundPage /></Suspense></ErrorBoundary> },
    ],
  },
]);

import { createBrowserRouter, Navigate } from "react-router";
import { lazy, Suspense } from "react";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageError } from "../components/PageError";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const DashboardPage = lazy(() => import("../pages/DashboardPage").then(m => ({ default: m.DashboardPage })));
const JobsPage = lazy(() => import("../pages/JobsPage").then(m => ({ default: m.JobsPage })));
const JobDetailPage = lazy(() => import("../pages/JobDetailPage").then(m => ({ default: m.JobDetailPage })));
const JobLogsPage = lazy(() => import("../pages/JobLogsPage").then(m => ({ default: m.JobLogsPage })));
const AgentsPage = lazy(() => import("../pages/AgentsPage").then(m => ({ default: m.AgentsPage })));
const AgentFormPage = lazy(() => import("../pages/AgentFormPage").then(m => ({ default: m.AgentFormPage })));
const SandboxesPage = lazy(() => import("../pages/SandboxesPage").then(m => ({ default: m.SandboxesPage })));
const SandboxFormPage = lazy(() => import("../pages/SandboxFormPage").then(m => ({ default: m.SandboxFormPage })));
const PipelinesPage = lazy(() => import("../pages/PipelinesPage").then(m => ({ default: m.PipelinesPage })));
const SettingsPage = lazy(() => import("../pages/SettingsPage").then(m => ({ default: m.SettingsPage })));
const NotFoundPage = lazy(() => import("../pages/NotFoundPage").then(m => ({ default: m.NotFoundPage })));
const IssuesPage = lazy(() => import("@/pages/IssuesPage").then((m) => ({ default: m.IssuesPage })));
const IssueDetailPage = lazy(() => import("@/pages/IssueDetailPage").then((m) => ({ default: m.IssueDetailPage })));
const EnvironmentsPage = lazy(() => import("@/pages/EnvironmentsPage").then((m) => ({ default: m.EnvironmentsPage })));

const fallback = <div className="p-6 text-muted-foreground">Loading...</div>;

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    errorElement: <PageError message="Something went wrong" />,
    children: [
      { index: true, element: <ErrorBoundary inline><Suspense fallback={fallback}><DashboardPage /></Suspense></ErrorBoundary> },
      { path: "jobs", element: <ErrorBoundary inline><Suspense fallback={fallback}><JobsPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId", element: <ErrorBoundary inline><Suspense fallback={fallback}><JobDetailPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId/logs", element: <ErrorBoundary inline><Suspense fallback={fallback}><JobLogsPage /></Suspense></ErrorBoundary> },
      { path: "agents", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentsPage /></Suspense></ErrorBoundary> },
      { path: "agents/new", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentFormPage /></Suspense></ErrorBoundary> },
      { path: "agents/:type", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentFormPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxesPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes/new", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxFormPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes/:name", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxFormPage /></Suspense></ErrorBoundary> },
      { path: "pipelines", element: <ErrorBoundary inline><Suspense fallback={fallback}><PipelinesPage /></Suspense></ErrorBoundary> },
      { path: "traces", element: <Navigate to="/jobs" replace /> },
      { path: "triage", element: <Navigate to="/settings" replace /> },
      { path: "issues", element: <ErrorBoundary inline><Suspense fallback={fallback}><IssuesPage /></Suspense></ErrorBoundary> },
      { path: "issues/:id", element: <ErrorBoundary inline><Suspense fallback={fallback}><IssueDetailPage /></Suspense></ErrorBoundary> },
      { path: "environments", element: <ErrorBoundary inline><Suspense fallback={fallback}><EnvironmentsPage /></Suspense></ErrorBoundary> },
      { path: "settings", element: <ErrorBoundary inline><Suspense fallback={fallback}><SettingsPage /></Suspense></ErrorBoundary> },
      { path: "*", element: <ErrorBoundary inline><Suspense fallback={fallback}><NotFoundPage /></Suspense></ErrorBoundary> },
    ],
  },
]);

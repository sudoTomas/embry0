import { createBrowserRouter, Navigate, useParams } from "react-router";
import { lazy, Suspense } from "react";
import { AppLayout } from "@/components/layout/AppLayout";
import { PageError } from "../components/PageError";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const DashboardPage = lazy(() => import("../pages/DashboardPage").then(m => ({ default: m.DashboardPage })));
const ConsolePage = lazy(() => import("../pages/ConsolePage").then(m => ({ default: m.ConsolePage })));
const JobsPage = lazy(() => import("../pages/JobsPage").then(m => ({ default: m.JobsPage })));
const JobDetailPage = lazy(() => import("../pages/JobDetailPage").then(m => ({ default: m.JobDetailPage })));
const MergeApprovalsPage = lazy(() => import("../pages/MergeApprovalsPage").then(m => ({ default: m.MergeApprovalsPage })));
const MergeApprovalPage = lazy(() => import("../pages/MergeApprovalPage").then(m => ({ default: m.MergeApprovalPage })));
const AgentsPage = lazy(() => import("../pages/AgentsPage").then(m => ({ default: m.AgentsPage })));
const AchievementsPage = lazy(() => import("../pages/AchievementsPage").then(m => ({ default: m.AchievementsPage })));
const AgentFormPage = lazy(() => import("../pages/AgentFormPage").then(m => ({ default: m.AgentFormPage })));
const SandboxesPage = lazy(() => import("../pages/SandboxesPage").then(m => ({ default: m.SandboxesPage })));
const SandboxFormPage = lazy(() => import("../pages/SandboxFormPage").then(m => ({ default: m.SandboxFormPage })));
const HostsPage = lazy(() => import("../pages/HostsPage").then(m => ({ default: m.HostsPage })));
const PipelinesPage = lazy(() => import("../pages/PipelinesPage").then(m => ({ default: m.PipelinesPage })));
const SettingsPage = lazy(() => import("../pages/SettingsPage").then(m => ({ default: m.SettingsPage })));
const NotFoundPage = lazy(() => import("../pages/NotFoundPage").then(m => ({ default: m.NotFoundPage })));
const IssuesPage = lazy(() => import("@/pages/IssuesPage").then((m) => ({ default: m.IssuesPage })));
const IssueDetailPage = lazy(() => import("@/pages/IssueDetailPage").then((m) => ({ default: m.IssueDetailPage })));
const EnvironmentsPage = lazy(() => import("@/pages/EnvironmentsPage").then((m) => ({ default: m.EnvironmentsPage })));
const SkillsPage = lazy(() => import("../pages/SkillsPage").then(m => ({ default: m.SkillsPage })));
const CredentialsPage = lazy(() => import("../pages/CredentialsPage").then(m => ({ default: m.CredentialsPage })));
const AboutOperationsPage = lazy(() => import("@/pages/AboutOperationsPage").then((m) => ({ default: m.AboutOperationsPage })));
const QaAppHistoryPage = lazy(() => import("../pages/QaAppHistoryPage").then(m => ({ default: m.QaAppHistoryPage })));
const QaRepoDetailPage = lazy(() => import("../pages/QaRepoDetailPage").then(m => ({ default: m.QaRepoDetailPage })));
const QaReposPage = lazy(() => import("../pages/QaReposPage").then(m => ({ default: m.QaReposPage })));
const QaRunDetailPage = lazy(() => import("../pages/QaRunDetailPage").then(m => ({ default: m.QaRunDetailPage })));
const QaAffectedSetPage = lazy(() => import("../pages/QaAffectedSetPage").then(m => ({ default: m.QaAffectedSetPage })));
const QaRepoCachePage = lazy(() => import("../pages/QaRepoCachePage").then(m => ({ default: m.QaRepoCachePage })));
const QaRepoFlakePage = lazy(() => import("../pages/QaRepoFlakePage").then(m => ({ default: m.QaRepoFlakePage })));
const QaRepoConfigPage = lazy(() => import("../pages/QaRepoConfigPage").then(m => ({ default: m.QaRepoConfigPage })));
const OnboardRepoPage = lazy(() => import("../pages/OnboardRepoPage").then(m => ({ default: m.OnboardRepoPage })));
const ReposPage = lazy(() => import("../pages/ReposPage").then(m => ({ default: m.ReposPage })));
const RepoDetailPage = lazy(() => import("../pages/RepoDetailPage").then(m => ({ default: m.RepoDetailPage })));
const OrganizationsPage = lazy(() => import("../pages/OrganizationsPage").then(m => ({ default: m.OrganizationsPage })));
const OrgWizardPage = lazy(() => import("../pages/OrgWizardPage").then(m => ({ default: m.OrgWizardPage })));
const OrganizationPage = lazy(() => import("../pages/OrganizationPage").then(m => ({ default: m.OrganizationPage })));
const StandardsPage = lazy(() => import("../pages/StandardsPage").then(m => ({ default: m.StandardsPage })));
const ProvidersPage = lazy(() => import("../pages/ProvidersPage").then(m => ({ default: m.ProvidersPage })));
const WatcherPage = lazy(() => import("../pages/WatcherPage").then(m => ({ default: m.WatcherPage })));
const LogsPage = lazy(() => import("../pages/LogsPage").then(m => ({ default: m.LogsPage })));
const UsersAdminPage = lazy(() => import("../pages/admin/UsersAdminPage").then(m => ({ default: m.UsersAdminPage })));
const AuditPage = lazy(() => import("../pages/admin/AuditPage").then(m => ({ default: m.AuditPage })));

const fallback = <div className="p-6 text-muted-foreground">Loading...</div>;

/** `/jobs/:jobId/logs` was the second job page; the one page carries the record now (spec 3.5). */
export function JobLogsPageRedirect() {
  const { jobId } = useParams<{ jobId: string }>();
  return <Navigate to={`/jobs/${jobId}`} replace />;
}

export const router = createBrowserRouter([
  {
    element: <AppLayout />,
    errorElement: <PageError message="Something went wrong" />,
    children: [
      { index: true, element: <ErrorBoundary inline><Suspense fallback={fallback}><DashboardPage /></Suspense></ErrorBoundary> },
      { path: "console", element: <ErrorBoundary inline><Suspense fallback={fallback}><ConsolePage /></Suspense></ErrorBoundary> },
      { path: "jobs", element: <ErrorBoundary inline><Suspense fallback={fallback}><JobsPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId", element: <ErrorBoundary inline><Suspense fallback={fallback}><JobDetailPage /></Suspense></ErrorBoundary> },
      { path: "jobs/:jobId/logs", element: <JobLogsPageRedirect /> },
      { path: "merge-approvals", element: <ErrorBoundary inline><Suspense fallback={fallback}><MergeApprovalsPage /></Suspense></ErrorBoundary> },
      { path: "merge-approvals/:approvalId", element: <ErrorBoundary inline><Suspense fallback={fallback}><MergeApprovalPage /></Suspense></ErrorBoundary> },
      { path: "agents", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentsPage /></Suspense></ErrorBoundary> },
      { path: "achievements", element: <ErrorBoundary inline><Suspense fallback={fallback}><AchievementsPage /></Suspense></ErrorBoundary> },
      { path: "agents/new", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentFormPage /></Suspense></ErrorBoundary> },
      { path: "agents/:type", element: <ErrorBoundary inline><Suspense fallback={fallback}><AgentFormPage /></Suspense></ErrorBoundary> },
      { path: "skills", element: <ErrorBoundary inline><Suspense fallback={fallback}><SkillsPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxesPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes/new", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxFormPage /></Suspense></ErrorBoundary> },
      { path: "sandboxes/:name", element: <ErrorBoundary inline><Suspense fallback={fallback}><SandboxFormPage /></Suspense></ErrorBoundary> },
      { path: "hosts", element: <ErrorBoundary inline><Suspense fallback={fallback}><HostsPage /></Suspense></ErrorBoundary> },
      { path: "pipelines", element: <ErrorBoundary inline><Suspense fallback={fallback}><PipelinesPage /></Suspense></ErrorBoundary> },
      { path: "issues", element: <ErrorBoundary inline><Suspense fallback={fallback}><IssuesPage /></Suspense></ErrorBoundary> },
      { path: "issues/:id", element: <ErrorBoundary inline><Suspense fallback={fallback}><IssueDetailPage /></Suspense></ErrorBoundary> },
      { path: "environments", element: <ErrorBoundary inline><Suspense fallback={fallback}><EnvironmentsPage /></Suspense></ErrorBoundary> },
      { path: "credentials", element: <ErrorBoundary inline><Suspense fallback={fallback}><CredentialsPage /></Suspense></ErrorBoundary> },
      { path: "qa/repos", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaReposPage /></Suspense></ErrorBoundary> },
      { path: "qa/repos/:repo/apps/:app", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaAppHistoryPage /></Suspense></ErrorBoundary> },
      { path: "qa/repos/:repo/cache", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaRepoCachePage /></Suspense></ErrorBoundary> },
      { path: "qa/repos/:repo/flake", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaRepoFlakePage /></Suspense></ErrorBoundary> },
      { path: "qa/repos/:repo/config", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaRepoConfigPage /></Suspense></ErrorBoundary> },
      { path: "qa/repos/:repo", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaRepoDetailPage /></Suspense></ErrorBoundary> },
      { path: "repos", element: <ErrorBoundary inline><Suspense fallback={fallback}><ReposPage /></Suspense></ErrorBoundary> },
      { path: "repos/onboard", element: <ErrorBoundary inline><Suspense fallback={fallback}><OnboardRepoPage /></Suspense></ErrorBoundary> },
      { path: "repos/*", element: <ErrorBoundary inline><Suspense fallback={fallback}><RepoDetailPage /></Suspense></ErrorBoundary> },
      { path: "orgs", element: <ErrorBoundary inline><Suspense fallback={fallback}><OrganizationsPage /></Suspense></ErrorBoundary> },
      { path: "orgs/-/new", element: <ErrorBoundary inline><Suspense fallback={fallback}><OrgWizardPage /></Suspense></ErrorBoundary> },
      { path: "orgs/:owner", element: <ErrorBoundary inline><Suspense fallback={fallback}><OrganizationPage /></Suspense></ErrorBoundary> },
      { path: "orgs/:owner/standards", element: <ErrorBoundary inline><Suspense fallback={fallback}><StandardsPage /></Suspense></ErrorBoundary> },
      { path: "qa/runs/:runId", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaRunDetailPage /></Suspense></ErrorBoundary> },
      { path: "qa/runs/:runId/affected", element: <ErrorBoundary inline><Suspense fallback={fallback}><QaAffectedSetPage /></Suspense></ErrorBoundary> },
      { path: "watcher", element: <ErrorBoundary inline><Suspense fallback={fallback}><WatcherPage /></Suspense></ErrorBoundary> },
      { path: "logs", element: <ErrorBoundary inline><Suspense fallback={fallback}><LogsPage /></Suspense></ErrorBoundary> },
      { path: "admin/users", element: <ErrorBoundary inline><Suspense fallback={fallback}><UsersAdminPage /></Suspense></ErrorBoundary> },
      { path: "admin/audit", element: <ErrorBoundary inline><Suspense fallback={fallback}><AuditPage /></Suspense></ErrorBoundary> },
      { path: "providers", element: <ErrorBoundary inline><Suspense fallback={fallback}><ProvidersPage /></Suspense></ErrorBoundary> },
      { path: "settings", element: <ErrorBoundary inline><Suspense fallback={fallback}><SettingsPage /></Suspense></ErrorBoundary> },
      { path: "about/operations", element: <ErrorBoundary inline><Suspense fallback={fallback}><AboutOperationsPage /></Suspense></ErrorBoundary> },
      { path: "*", element: <ErrorBoundary inline><Suspense fallback={fallback}><NotFoundPage /></Suspense></ErrorBoundary> },
    ],
  },
]);

import CaseTable from "./components/CaseTable";
import CaseDetail from "./components/CaseDetail";
import ConsoleView from "./components/ConsoleView";
import AnalyticsView from "./components/AnalyticsView";
import ExceptionsView from "./components/ExceptionsView";
import OverviewView from "./components/OverviewView";
import LiveAuditFeed from "./components/LiveAuditFeed";
import Sidebar from "./components/Sidebar";
import { RecoveryDataProvider } from "./hooks/useRecoveryData";
import { ActiveCaseProvider } from "./hooks/useActiveCase";
import { ViewNavProvider, useViewNav } from "./hooks/useViewNav";

const VIEW_LABELS = {
  overview: "Overview",
  "recovery-queue": "Recovery queue",
  "live-agent": "Live agent",
  analytics: "Recovery analytics",
  "audit-trail": "Audit trail",
  exceptions: "Exceptions",
};

// All 6 views are real, independent view containers — no scroll anchors, no
// scrollIntoView. Every one stays mounted at all times (CSS `hidden` toggle
// only) so in-progress state — form drafts, the reasoning panel's reveal
// sequence, filters, the selected case — is never destroyed by navigating
// away and back.
function AppShell() {
  const { activeView } = useViewNav();

  return (
    <div className="min-h-screen bg-[var(--color-paper)] flex flex-col lg:flex-row">
      <Sidebar />

      <div className="flex-1 min-w-0">
        <div className="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
          <header className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between border-b border-[var(--color-line)] pb-4">
            <div className="flex items-center gap-2 text-xs text-[var(--color-ink-400)]">
              <span>Workspace</span>
              <span>/</span>
              <span className="font-medium text-[var(--color-ink-700)]">{VIEW_LABELS[activeView]}</span>
            </div>
            <p className="text-xs text-[var(--color-ink-400)]">
              Live view over the shared recovery engine — six distinct operational views, one data layer.
            </p>
          </header>

          <div className={activeView === "overview" ? "" : "hidden"}>
            <OverviewView />
          </div>

          <div className={activeView === "recovery-queue" ? "grid grid-cols-1 lg:grid-cols-3 gap-4" : "hidden"}>
            <div className="lg:col-span-2">
              <CaseTable />
            </div>
            <div className="lg:col-span-1">
              <CaseDetail />
            </div>
          </div>

          <div className={activeView === "live-agent" ? "" : "hidden"}>
            <ConsoleView />
          </div>

          <div className={activeView === "analytics" ? "" : "hidden"}>
            <AnalyticsView />
          </div>

          <div className={activeView === "audit-trail" ? "space-y-4" : "hidden"}>
            <div>
              <h2 className="font-serif text-lg text-[var(--color-ink-900)]">Audit trail</h2>
              <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
                The global chronological ledger — what the system did, when, and under which authority.
              </p>
            </div>
            <LiveAuditFeed />
          </div>

          <div className={activeView === "exceptions" ? "" : "hidden"}>
            <ExceptionsView />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <RecoveryDataProvider>
      <ActiveCaseProvider>
        <ViewNavProvider>
          <AppShell />
        </ViewNavProvider>
      </ActiveCaseProvider>
    </RecoveryDataProvider>
  );
}
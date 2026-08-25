import { useState } from "react";
import MetricsPanel from "./components/MetricsPanel";
import CaseTable from "./components/CaseTable";
import CaseDetail from "./components/CaseDetail";
import ConsoleView from "./components/ConsoleView";

export default function App() {
  const [tab, setTab] = useState("dashboard");
  const [selectedId, setSelectedId] = useState(null);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-7xl mx-auto p-6 space-y-6">
        <header className="flex items-center justify-between border-b border-gray-200 pb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">Revenue Recovery</h1>
            <p className="text-xs text-gray-400 mt-0.5">Live view over the shared recovery engine.</p>
          </div>
          <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
            <button
              onClick={() => setTab("dashboard")}
              className={`text-sm px-4 py-1.5 rounded-md font-medium transition-colors ${
                tab === "dashboard"
                  ? "bg-white text-indigo-700 shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              Ops Dashboard
            </button>
            <button
              onClick={() => setTab("console")}
              className={`text-sm px-4 py-1.5 rounded-md font-medium transition-colors ${
                tab === "console"
                  ? "bg-white text-indigo-700 shadow-sm"
                  : "text-gray-500 hover:text-gray-700"
              }`}
            >
              Live Console
            </button>
          </div>
        </header>

        {tab === "dashboard" && (
          <>
            <MetricsPanel />
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <div className="lg:col-span-2">
                <CaseTable onSelectCase={setSelectedId} selectedId={selectedId} />
              </div>
              <div className="lg:col-span-1">
                <CaseDetail paymentId={selectedId} />
              </div>
            </div>
          </>
        )}

        {tab === "console" && <ConsoleView />}
      </div>
    </div>
  );
}
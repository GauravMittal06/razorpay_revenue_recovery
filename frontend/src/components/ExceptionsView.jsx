import { useState } from "react";
import { useRecoveryData } from "../hooks/useRecoveryData";
import ExceptionsPanel from "./ExceptionsPanel";
import CaseTable from "./CaseTable";
import CaseDetail from "./CaseDetail";
import { LoadingState, ErrorState } from "./LoadingErrorStates";

export default function ExceptionsView() {
  const { metrics, metricsLoading, metricsError } = useRecoveryData();
  const [selectedFlag, setSelectedFlag] = useState(null);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-[var(--color-ink-900)]">Exceptions</h2>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
          Which cases needed a human, why, and where to investigate them.
        </p>
      </div>

      {metricsError && <ErrorState message={metricsError} />}
      {metricsLoading && !metrics && !metricsError && <LoadingState label="Loading exceptions…" />}

      {metrics && (
        <ExceptionsPanel
          count={metrics.unresolved_exceptions_count}
          byFlagType={metrics.unresolved_exceptions_by_flag_type}
          selectedFlag={selectedFlag}
          onSelectFlag={(f) => setSelectedFlag((cur) => (cur === f ? null : f))}
        />
      )}

      {selectedFlag && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-ink-500)]">
          <span>
            Table below is further narrowed to cases whose latest action was flagged{" "}
            <span className="font-data font-medium text-[var(--color-ink-800)]">{selectedFlag}</span>.
          </span>
          <button
            onClick={() => setSelectedFlag(null)}
            className="text-[var(--color-signal-700)] font-medium hover:underline"
          >
            Clear
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <CaseTable clientFilter={selectedFlag ? (c) => c.flag_type === selectedFlag : undefined} />
        </div>
        <div className="lg:col-span-1">
          <CaseDetail />
        </div>
      </div>
    </div>
  );
}
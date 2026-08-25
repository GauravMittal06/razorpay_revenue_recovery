import { useEffect, useState } from "react";
import { fetchMetrics } from "../api/client";
import RootCauseChart from "./RootCauseChart";
import TimeToRecoveryChart from "./TimeToRecoveryChart";
import ExceptionsPanel from "./ExceptionsPanel";
import { LoadingState, ErrorState } from "./LoadingErrorStates";

const CARD_ACCENTS = {
  recovery: "border-l-green-500",
  value: "border-l-indigo-500",
  exposure: "border-l-amber-500",
  exceptions: "border-l-red-500",
};

function HeadlineCard({ label, value, sub, accent }) {
  return (
    <div className={`bg-white rounded-lg border border-gray-200 border-l-4 ${accent} shadow-sm p-4`}>
      <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{label}</div>
      <div className="text-2xl font-bold text-gray-900">{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}

export default function MetricsPanel() {
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const data = await fetchMetrics();
      setMetrics(data);
      setError(null);
    } catch (e) {
      setError("Could not reach /api/metrics. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-gray-900">Metrics</h2>
        <button
          onClick={load}
          disabled={loading}
          className="text-xs px-3 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <ErrorState message={error} />}
      {loading && !metrics && !error && <LoadingState label="Loading metrics…" />}

      {metrics && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <HeadlineCard
              label="Overall Recovery Rate"
              value={`${metrics.overall_recovery_rate_pct}%`}
              accent={CARD_ACCENTS.recovery}
            />
            <HeadlineCard
              label="₹ Recovered / At Risk"
              value={`${metrics.recovery_value_pct}%`}
              sub={`₹${metrics.amount_recovered.toLocaleString()} / ₹${metrics.amount_at_risk_total.toLocaleString()}`}
              accent={CARD_ACCENTS.value}
            />
            <HeadlineCard
              label="Current Amount Exposed"
              value={`₹${metrics.current_amount_exposed.toLocaleString()}`}
              accent={CARD_ACCENTS.exposure}
            />
            <HeadlineCard
              label="Unresolved Exceptions"
              value={metrics.unresolved_exceptions_count}
              accent={CARD_ACCENTS.exceptions}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <RootCauseChart recoveryByRootCause={metrics.recovery_by_root_cause} />
            <TimeToRecoveryChart distribution={metrics.time_to_recovery_distribution} />
          </div>

          <ExceptionsPanel
            count={metrics.unresolved_exceptions_count}
            byFlagType={metrics.unresolved_exceptions_by_flag_type}
          />
        </>
      )}
    </div>
  );
}
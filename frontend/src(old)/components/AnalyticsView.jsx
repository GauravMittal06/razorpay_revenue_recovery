import { useRecoveryData } from "../hooks/useRecoveryData";
import RootCauseChart from "./RootCauseChart";
import TimeToRecoveryChart from "./TimeToRecoveryChart";
import { LoadingState, ErrorState } from "./LoadingErrorStates";

function RootCauseTable({ data }) {
  const rows = Object.entries(data || {});
  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm overflow-hidden">
      <div className="px-3 pt-3">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Exact figures</h3>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-2">Same data as the chart, without reading tooltips.</p>
      </div>
      <table className="min-w-full text-xs">
        <thead className="bg-[var(--color-paper)]">
          <tr className="text-left eyebrow">
            <th className="px-3 py-2">Root cause</th>
            <th className="px-3 py-2">Recovered</th>
            <th className="px-3 py-2">Total</th>
            <th className="px-3 py-2">Rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([cause, v]) => (
            <tr key={cause} className="border-t border-[var(--color-line-soft)]">
              <td className="px-3 py-2 text-[var(--color-ink-700)]">{cause}</td>
              <td className="px-3 py-2 font-data text-[var(--color-ink-900)]">{v.recovered}</td>
              <td className="px-3 py-2 font-data text-[var(--color-ink-500)]">{v.total}</td>
              <td className="px-3 py-2 font-data font-medium text-[var(--color-ink-900)]">{v.recovery_rate_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimeToRecoveryTable({ data }) {
  const rows = Object.entries(data || {});
  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm overflow-hidden">
      <div className="px-3 pt-3">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Exact figures</h3>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-2">Same data as the chart, without reading tooltips.</p>
      </div>
      <table className="min-w-full text-xs">
        <thead className="bg-[var(--color-paper)]">
          <tr className="text-left eyebrow">
            <th className="px-3 py-2">Event type</th>
            <th className="px-3 py-2">&lt;1d</th>
            <th className="px-3 py-2">1-3d</th>
            <th className="px-3 py-2">3-7d</th>
            <th className="px-3 py-2">7d+</th>
            <th className="px-3 py-2">Recovered</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([type, b]) => (
            <tr key={type} className="border-t border-[var(--color-line-soft)]">
              <td className="px-3 py-2 text-[var(--color-ink-700)]">{type}</td>
              <td className="px-3 py-2 font-data">{b["<1d"]}</td>
              <td className="px-3 py-2 font-data">{b["1-3d"]}</td>
              <td className="px-3 py-2 font-data">{b["3-7d"]}</td>
              <td className="px-3 py-2 font-data">{b["7d+"]}</td>
              <td className="px-3 py-2 font-data font-medium">{b.recovered_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// "Why is recovery performing this way?" — the only view with exact
// per-category breakdowns. No KPI cards (those are Overview's job), no
// duplication of what Overview already shows.
export default function AnalyticsView() {
  const { metrics, metricsLoading, metricsError } = useRecoveryData();

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-[var(--color-ink-900)]">Recovery analytics</h2>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
          Root-cause and timing breakdowns that explain the headline numbers on Overview — not shown there.
        </p>
      </div>

      {metricsError && <ErrorState message={metricsError} />}
      {metricsLoading && !metrics && !metricsError && <LoadingState label="Loading analytics…" />}

      {metrics && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <RootCauseChart recoveryByRootCause={metrics.recovery_by_root_cause} />
            <RootCauseTable data={metrics.recovery_by_root_cause} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <TimeToRecoveryChart distribution={metrics.time_to_recovery_distribution} />
            <TimeToRecoveryTable data={metrics.time_to_recovery_distribution} />
          </div>
        </>
      )}
    </div>
  );
}
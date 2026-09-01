import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { EmptyState } from "./LoadingErrorStates";

export default function RootCauseChart({ recoveryByRootCause }) {
  if (!recoveryByRootCause) return null;

  const data = Object.entries(recoveryByRootCause).map(([root_cause, v]) => ({
    root_cause,
    recovery_rate_pct: v.recovery_rate_pct,
    recovered: v.recovered,
    total: v.total,
  }));

  const hasAnyData = data.some((d) => d.total > 0);

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">
        Recovery rate by root cause
      </h3>
      <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-3">
        Which failure reasons the engine is actually recovering from, vs. which are landing but not converting.
      </p>
      {!hasAnyData ? (
        <EmptyState message="No payment_failed cases recorded yet." />
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef0f5" />
            <XAxis dataKey="root_cause" tick={{ fontSize: 10, fill: "#64748b" }} interval={0} angle={-20} textAnchor="end" height={60} />
            <YAxis unit="%" tick={{ fontSize: 11, fill: "#64748b" }} />
            <Tooltip
              formatter={(value, name, props) =>
                name === "recovery_rate_pct"
                  ? [`${value}% (${props.payload.recovered}/${props.payload.total})`, "Recovery rate"]
                  : value
              }
              contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e4e7ee" }}
            />
            <Bar dataKey="recovery_rate_pct" fill="#3730a3" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
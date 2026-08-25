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
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
      <h3 className="text-sm font-semibold text-gray-800 mb-3">
        Recovery Rate by Root Cause
      </h3>
      {!hasAnyData ? (
        <EmptyState message="No payment_failed cases recorded yet." />
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="root_cause" tick={{ fontSize: 10, fill: "#6b7280" }} interval={0} angle={-20} textAnchor="end" height={60} />
            <YAxis unit="%" tick={{ fontSize: 11, fill: "#6b7280" }} />
            <Tooltip
              formatter={(value, name, props) =>
                name === "recovery_rate_pct"
                  ? [`${value}% (${props.payload.recovered}/${props.payload.total})`, "Recovery rate"]
                  : value
              }
              contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
            />
            <Bar dataKey="recovery_rate_pct" fill="#4f46e5" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
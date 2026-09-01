import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid } from "recharts";
import { EmptyState } from "./LoadingErrorStates";

const BUCKET_COLORS = {
  "<1d": "#16a34a",
  "1-3d": "#3b82f6",
  "3-7d": "#f59e0b",
  "7d+": "#ef4444",
};

export default function TimeToRecoveryChart({ distribution }) {
  if (!distribution) return null;

  const data = Object.entries(distribution).map(([event_type, buckets]) => ({
    event_type,
    "<1d": buckets["<1d"],
    "1-3d": buckets["1-3d"],
    "3-7d": buckets["3-7d"],
    "7d+": buckets["7d+"],
    recovered_count: buckets.recovered_count,
  }));

  const hasAnyData = data.some((d) => d.recovered_count > 0);

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">
        Time-to-recovery distribution
      </h3>
      <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-3">
        How long recovered cases took, by event type — a wide spread toward 7d+ signals a slow recovery path worth tightening.
      </p>
      {!hasAnyData ? (
        <EmptyState message="No recovered cases yet." />
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef0f5" />
            <XAxis dataKey="event_type" tick={{ fontSize: 10, fill: "#64748b" }} />
            <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#64748b" }} />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e4e7ee" }} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {Object.keys(BUCKET_COLORS).map((bucket) => (
              <Bar key={bucket} dataKey={bucket} stackId="a" fill={BUCKET_COLORS[bucket]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
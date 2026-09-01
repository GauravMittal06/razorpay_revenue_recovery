import { useRecoveryData } from "../hooks/useRecoveryData";
import { useViewNav } from "../hooks/useViewNav";

export default function AttentionPanel() {
  const { metrics, cases } = useRecoveryData();
  const { navigateTo } = useViewNav();

  if (!metrics) return null;

  const exceptions = metrics.unresolved_exceptions_count;
  const escalated = cases.filter((c) => c.recovery_status === "escalated").length;
  const isCalm = exceptions === 0 && escalated === 0;

  const topFlags = Object.entries(metrics.unresolved_exceptions_by_flag_type || {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([flag]) => flag);

  return (
    <div
      className={`rounded-lg border shadow-sm p-4 ${
        isCalm ? "bg-[var(--color-paper)] border-[var(--color-line)]" : "bg-amber-50 border-amber-200"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Recovery posture</h3>
          <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
            What needs attention right now, and where to go next.
          </p>
        </div>
        <span
          className={`shrink-0 text-[10px] font-semibold uppercase tracking-wide px-2 py-1 rounded-full ${
            isCalm
              ? "bg-green-50 text-green-700"
              : escalated > 0
              ? "bg-red-50 text-red-700"
              : "bg-amber-50 text-amber-700"
          }`}
        >
          {isCalm ? "All clear" : escalated > 0 ? "Needs attention" : "Monitor"}
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
        <button
          onClick={() => navigateTo("exceptions")}
          className="text-left bg-white/70 rounded-md p-3 hover:bg-white transition-colors"
        >
          <div className="font-data text-xl font-semibold text-[var(--color-ink-900)]">{exceptions}</div>
          <div className="text-xs text-[var(--color-ink-500)] mt-0.5">
            flagged for manual review
            {topFlags.length > 0 && <> — mostly {topFlags.join(", ")}</>}
          </div>
          <div className="text-[11px] text-[var(--color-signal-700)] font-medium mt-1.5">
            Investigate in Exceptions →
          </div>
        </button>

        <button
          onClick={() => navigateTo("recovery-queue")}
          className="text-left bg-white/70 rounded-md p-3 hover:bg-white transition-colors"
        >
          <div className="font-data text-xl font-semibold text-[var(--color-ink-900)]">{escalated}</div>
          <div className="text-xs text-[var(--color-ink-500)] mt-0.5">escalated cases open right now</div>
          <div className="text-[11px] text-[var(--color-signal-700)] font-medium mt-1.5">
            Open in Recovery Queue →
          </div>
        </button>
      </div>
    </div>
  );
}
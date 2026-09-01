import { useRecoveryData } from "../hooks/useRecoveryData";
import { useHighlightOnChange } from "../hooks/useHighlightOnChange";
import { LoadingState, ErrorState } from "./LoadingErrorStates";

const ACCENTS = {
  recovery: "text-green-600",
  value: "text-[var(--color-signal-600)]",
  exposure: "text-amber-600",
  exceptions: "text-[var(--color-ink-900)]",
};

// V0-inspired large-serif stat presentation, same underlying HeadlineCard
// contract (label/value/sub/caption) and the same real useHighlightOnChange
// pulse — only the type treatment changes.
function HeadlineCard({ label, caption, value, sub, accent }) {
  const changed = useHighlightOnChange(value);
  return (
    <div
      className={`border-t-2 border-[var(--color-ink-900)]/10 pt-4 ${changed ? "highlight-pulse rounded-lg px-2 -mx-2" : ""}`}
    >
      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--color-ink-400)]">{label}</p>
      <p className={`mt-2 font-serif text-3xl font-light tracking-[-0.02em] sm:text-4xl ${ACCENTS[accent]}`}>
        {value}
      </p>
      {sub && <p className="font-mono text-[11px] text-[var(--color-ink-400)] mt-1.5">{sub}</p>}
      {caption && <p className="text-xs text-[var(--color-ink-400)] mt-1.5 leading-snug max-w-[16rem]">{caption}</p>}
    </div>
  );
}

// Pure KPI strip — the "system state at a glance" part of Overview. Charts
// and the exceptions breakdown live elsewhere now (Analytics, Exceptions)
// so this component isn't duplicated across views.
export default function MetricsPanel() {
  const { metrics, metricsLoading, metricsError, refetchAll } = useRecoveryData();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-[var(--color-ink-900)]">System state</h2>
          <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
            Every figure below traces to a live database query — nothing here is pre-computed or fixed.
          </p>
        </div>
        <button
          onClick={refetchAll}
          disabled={metricsLoading}
          className="text-xs px-3 py-1.5 rounded-md border border-[var(--color-line)] text-[var(--color-ink-500)] hover:bg-[var(--color-paper)] disabled:opacity-50 transition-colors"
        >
          {metricsLoading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {metricsError && <ErrorState message={metricsError} />}
      {metricsLoading && !metrics && !metricsError && <LoadingState label="Loading metrics…" />}

      {metrics && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-5">
          <HeadlineCard
            label="Overall recovery rate"
            value={`${metrics.overall_recovery_rate_pct}%`}
            caption="Share of at-risk payments the engine has recovered so far."
            accent="recovery"
          />
          <HeadlineCard
            label="₹ recovered / at risk"
            value={`${metrics.recovery_value_pct}%`}
            sub={`₹${metrics.amount_recovered.toLocaleString()} / ₹${metrics.amount_at_risk_total.toLocaleString()}`}
            caption="The headline number — money actually clawed back vs. total exposure."
            accent="value"
          />
          <HeadlineCard
            label="Amount currently exposed"
            value={`₹${metrics.current_amount_exposed.toLocaleString()}`}
            caption="Still open, unrecovered, live right now."
            accent="exposure"
          />
          <HeadlineCard
            label="Unresolved exceptions"
            value={metrics.unresolved_exceptions_count}
            caption="Cases the engine flagged for a human — see the Exceptions view for why."
            accent="exceptions"
          />
        </div>
      )}
    </div>
  );
}
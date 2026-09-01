import { useRecoveryData } from "../hooks/useRecoveryData";
import { useHighlightOnChange } from "../hooks/useHighlightOnChange";
import { LoadingState, ErrorState } from "./LoadingErrorStates";

const ACCENTS = {
  recovery: "border-l-green-500",
  value: "border-l-[var(--color-signal-600)]",
  exposure: "border-l-amber-500",
  exceptions: "border-l-red-500",
};

function HeadlineCard({ label, caption, value, sub, accent, hero }) {
  const changed = useHighlightOnChange(value);
  return (
    <div
      className={`bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] border-l-[3px] ${accent} shadow-sm ${
        hero ? "p-5" : "p-4"
      } ${changed ? "highlight-pulse" : ""}`}
    >
      <div className="eyebrow mb-1.5">{label}</div>
      <div className={`font-data font-semibold text-[var(--color-ink-900)] ${hero ? "text-3xl" : "text-xl"}`}>
        {value}
      </div>
      {sub && <div className="font-data text-xs text-[var(--color-ink-400)] mt-1.5">{sub}</div>}
      {caption && <div className="text-xs text-[var(--color-ink-400)] mt-1.5 leading-snug">{caption}</div>}
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
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <HeadlineCard
            hero
            label="Overall recovery rate"
            value={`${metrics.overall_recovery_rate_pct}%`}
            caption="Share of at-risk payments the engine has recovered so far."
            accent={ACCENTS.recovery}
          />
          <HeadlineCard
            hero
            label="₹ recovered / at risk"
            value={`${metrics.recovery_value_pct}%`}
            sub={`₹${metrics.amount_recovered.toLocaleString()} / ₹${metrics.amount_at_risk_total.toLocaleString()}`}
            caption="The headline number — money actually clawed back vs. total exposure."
            accent={ACCENTS.value}
          />
          <HeadlineCard
            label="Amount currently exposed"
            value={`₹${metrics.current_amount_exposed.toLocaleString()}`}
            caption="Still open, unrecovered, live right now."
            accent={ACCENTS.exposure}
          />
          <HeadlineCard
            label="Unresolved exceptions"
            value={metrics.unresolved_exceptions_count}
            caption="Cases the engine flagged for a human — see the Exceptions view for why."
            accent={ACCENTS.exceptions}
          />
        </div>
      )}
    </div>
  );
}
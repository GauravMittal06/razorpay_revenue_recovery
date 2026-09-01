const FLAG_DESCRIPTIONS = {
  dispute_flag: "Customer indicated a dispute in their reply — routed to a human instead of continuing automated recovery.",
  root_cause_update_candidate: "The engine suspects the recorded root cause may be wrong and wants a human to confirm before continuing.",
  mismatch: "A recovered amount or detail didn't match what was expected — needs verification.",
  none: "Flagged for manual review without a more specific reason code recorded.",
};
const FALLBACK_DESCRIPTION = "Flagged for manual review.";

// `selectedFlag` / `onSelectFlag`: optional — when provided, each reason
// card becomes a real client-side quick filter into the case table below,
// using the case's own flag_type field.
export default function ExceptionsPanel({ count, byFlagType, selectedFlag, onSelectFlag }) {
  if (byFlagType == null) return null;

  const isZero = count === 0;
  const entries = Object.entries(byFlagType);

  return (
    <div
      className={`rounded-lg border shadow-sm p-4 ${
        isZero ? "bg-[var(--color-paper)] border-[var(--color-line)]" : "bg-amber-50 border-amber-200"
      }`}
    >
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Unresolved exceptions</h3>
        <span className={`font-data text-2xl font-bold ${isZero ? "text-[var(--color-ink-300)]" : "text-amber-700"}`}>
          {count}
        </span>
      </div>
      <p className="text-xs text-[var(--color-ink-400)] mb-3">
        Cases flagged for manual review. Each card below shows the <em>latest recorded action's</em> flag —
        a case's flag can change as new actions are logged against it. Human resolution isn't tracked yet, so this
        isn't a resolved/unresolved queue.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {entries.map(([flag, n]) => {
          const clickable = !!onSelectFlag;
          const isSelected = selectedFlag === flag;
          const Wrapper = clickable ? "button" : "div";
          return (
            <Wrapper
              key={flag}
              onClick={clickable ? () => onSelectFlag(flag) : undefined}
              className={`text-left rounded-md p-3 border transition-colors ${
                isSelected
                  ? "bg-[var(--color-signal-50)] border-[var(--color-signal-600)]"
                  : n > 0
                  ? "bg-white/80 border-amber-200 hover:bg-white"
                  : "bg-white/40 border-[var(--color-line)] text-[var(--color-ink-300)]"
              } ${clickable ? "cursor-pointer" : ""}`}
            >
              <div className="flex items-center justify-between">
                <span className="font-data text-xs font-semibold text-[var(--color-ink-800)]">{flag}</span>
                <span className={`font-data text-sm font-bold ${n > 0 ? "text-[var(--color-ink-900)]" : "text-[var(--color-ink-300)]"}`}>
                  {n}
                </span>
              </div>
              <p className="text-[11px] text-[var(--color-ink-500)] mt-1 leading-snug">
                {FLAG_DESCRIPTIONS[flag] || FALLBACK_DESCRIPTION}
              </p>
              {clickable && n > 0 && (
                <div className="text-[10px] font-medium text-[var(--color-signal-700)] mt-1.5">
                  {isSelected ? "Showing these cases below ✓" : "View these cases →"}
                </div>
              )}
            </Wrapper>
          );
        })}
      </div>
    </div>
  );
}
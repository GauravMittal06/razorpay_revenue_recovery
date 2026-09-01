import { useRecoveryData } from "../hooks/useRecoveryData";
import { useActiveCase } from "../hooks/useActiveCase";
import { useHighlightOnChange, useHighlightNewItems } from "../hooks/useHighlightOnChange";
import FiltersBar from "./FiltersBar";
import { Badge, badgeClass, RECOVERY_STATUS_STYLES, OUTCOME_STYLES } from "../statusColors";
import { LoadingState, ErrorState, EmptyState } from "./LoadingErrorStates";

function CaseRow({ c, isNew, isSelected, index, onSelect }) {
  const changed = useHighlightOnChange({
    status: c.recovery_status,
    outcome: c.outcome,
    action: c.action_type,
  });
  const highlighted = isNew || changed;

  return (
    <tr
      onClick={() => onSelect(c.id)}
      className={`cursor-pointer border-t border-[var(--color-line-soft)] transition-colors ${
        highlighted ? "highlight-pulse" : ""
      } ${
        isSelected
          ? "bg-[var(--color-signal-50)]"
          : index % 2 === 0
          ? "bg-white hover:bg-[var(--color-paper)]"
          : "bg-[var(--color-paper)]/60 hover:bg-[var(--color-paper)]"
      }`}
    >
      <td className="px-3 py-2 font-data text-xs text-[var(--color-ink-400)]">{c.id}</td>
      <td className="px-3 py-2 text-[var(--color-ink-700)]">{c.customer_name || "—"}</td>
      <td className="px-3 py-2 font-data text-[var(--color-ink-900)] font-medium">
        ₹{c.amount?.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-[var(--color-ink-500)]">{c.event_type}</td>
      <td className="px-3 py-2">
        <Badge className={badgeClass(RECOVERY_STATUS_STYLES, c.recovery_status)}>
          {c.recovery_status}
        </Badge>
      </td>
      <td className="px-3 py-2 text-[var(--color-ink-500)]">{c.action_type || "—"}</td>
      <td className="px-3 py-2">
        {c.outcome ? (
          <Badge className={badgeClass(OUTCOME_STYLES, c.outcome)}>{c.outcome}</Badge>
        ) : (
          <span className="text-[var(--color-ink-300)]">—</span>
        )}
      </td>
    </tr>
  );
}

// `clientFilter`: optional predicate applied on top of the already-fetched
// `cases` array, entirely client-side. Used by ExceptionsView to quick-filter
// by flag_type — a field the API already returns per case, but not one the
// server-side query params (event_type/recovery_status/outcome) support, so
// this stays purely presentational rather than touching the API layer.
export default function CaseTable({ clientFilter }) {
  const { cases, casesLoading, casesError, caseFilters, setCaseFilters } = useRecoveryData();
  const { activeCaseId, setActiveCaseId } = useActiveCase();

  const visibleCases = clientFilter ? cases.filter(clientFilter) : cases;

  // resetSignal: re-baseline whenever the filter set changes, so swapping
  // filters never reads as "every visible row is new".
  const newKeys = useHighlightNewItems(visibleCases, (c) => c.id, JSON.stringify(caseFilters) + !!clientFilter);

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <div className="flex items-center justify-between mb-1">
        <h2 className="font-serif text-lg text-[var(--color-ink-900)]">Cases</h2>
        <span className="font-data text-xs text-[var(--color-ink-400)]">{visibleCases.length} shown</span>
      </div>
      <p className="text-xs text-[var(--color-ink-400)] mb-3">
        Select a row to inspect its full audit trail. Rows pulse briefly when the engine updates them.
      </p>

      <div className="mb-3">
        <FiltersBar filters={caseFilters} setFilters={setCaseFilters} />
      </div>

      {casesError && <ErrorState message={casesError} />}
      {casesLoading && !casesError && <LoadingState label="Loading cases…" />}

      {!casesLoading && !casesError && visibleCases.length === 0 && (
        <EmptyState message="No cases match these filters." />
      )}

      {!casesLoading && !casesError && visibleCases.length > 0 && (
        <div className="overflow-x-auto max-h-[420px] overflow-y-auto rounded-md border border-[var(--color-line-soft)]">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 bg-[var(--color-paper)] z-10">
              <tr className="text-left eyebrow">
                <th className="px-3 py-2">ID</th>
                <th className="px-3 py-2">Customer</th>
                <th className="px-3 py-2">Amount</th>
                <th className="px-3 py-2">Event type</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Last action</th>
                <th className="px-3 py-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {visibleCases.map((c, i) => (
                <CaseRow
                  key={c.id}
                  c={c}
                  index={i}
                  isNew={newKeys.has(c.id)}
                  isSelected={activeCaseId === c.id}
                  onSelect={setActiveCaseId}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
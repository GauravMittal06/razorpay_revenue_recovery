import { useRecoveryData } from "../hooks/useRecoveryData";
import { useViewNav } from "../hooks/useViewNav";
import { Badge, badgeClass, OUTCOME_STYLES, AuthorityTag } from "../statusColors";
import { EmptyState } from "./LoadingErrorStates";

export default function RecentActivityPreview() {
  const { auditFeed } = useRecoveryData();
  const { navigateTo } = useViewNav();
  const recent = auditFeed.slice(0, 3);

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Recent activity</h3>
        <button
          onClick={() => navigateTo("audit-trail")}
          className="text-xs font-medium text-[var(--color-signal-700)] hover:underline"
        >
          Full audit trail →
        </button>
      </div>
      <p className="text-xs text-[var(--color-ink-400)] mb-3">
        The last few actions the engine logged — a glimpse, not the ledger.
      </p>

      {recent.length === 0 ? (
        <EmptyState message="No actions logged yet." />
      ) : (
        <ul className="text-xs space-y-2">
          {recent.map((a) => (
            <li key={a.action_id} className="border-l-2 border-[var(--color-line)] pl-2 py-0.5">
              <div className="font-data text-[var(--color-ink-300)]">{a.payment_id}</div>
              <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
                <span className="text-[var(--color-ink-700)] font-medium">{a.action_type || "—"}</span>
                <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                <AuthorityTag triggeredBy={a.triggered_by} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
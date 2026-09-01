import { useEffect, useState } from "react";
import { fetchAuditFeed } from "../api/client";
import { Badge, badgeClass, OUTCOME_STYLES, FLAG_STYLES, AuthorityTag } from "../statusColors";
import { LoadingState, EmptyState } from "./LoadingErrorStates";

export default function LiveAuditFeed({ refreshKey }) {
  const [feed, setFeed] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAuditFeed(20)
      .then(setFeed)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [refreshKey]);

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
        <h3 className="font-serif text-base text-[var(--color-ink-900)]">Live audit feed</h3>
      </div>

      {loading && <LoadingState label="Loading feed…" />}

      {!loading && feed.length === 0 && <EmptyState message="No actions logged yet." />}

      {!loading && feed.length > 0 && (
        <ol className="relative border-l border-[var(--color-line)] ml-1 space-y-4 max-h-[420px] overflow-y-auto pr-1">
          {feed.map((a) => (
            <li key={a.action_id} className="pl-4 relative text-xs">
              <span className="absolute -left-[4.5px] top-1 size-2 rounded-full bg-[var(--color-signal-600)] ring-4 ring-[var(--color-surface)]" />
              <div className="font-mono text-[10px] text-[var(--color-ink-300)]">{a.payment_id}</div>
              <div className="flex items-center gap-1.5 flex-wrap mt-1">
                <span className="text-[var(--color-ink-700)] font-medium">{a.action_type || "—"}</span>
                <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                {a.flag_type && (
                  <Badge className={badgeClass(FLAG_STYLES, a.flag_type)}>{a.flag_type}</Badge>
                )}
                <AuthorityTag triggeredBy={a.triggered_by} />
              </div>
              <div className="font-mono text-[10px] text-[var(--color-ink-300)] mt-1">
                {new Date(a.timestamp * 1000).toLocaleString()}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
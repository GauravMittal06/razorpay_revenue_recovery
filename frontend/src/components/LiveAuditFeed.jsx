import { useEffect, useState } from "react";
import { fetchAuditFeed } from "../api/client";
import { Badge, badgeClass, OUTCOME_STYLES, FLAG_STYLES } from "../statusColors";
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
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
        <h3 className="text-sm font-semibold text-gray-900">Live Audit Feed</h3>
      </div>

      {loading && <LoadingState label="Loading feed…" />}

      {!loading && feed.length === 0 && <EmptyState message="No actions logged yet." />}

      {!loading && feed.length > 0 && (
        <ul className="text-xs space-y-2 max-h-[300px] overflow-y-auto">
          {feed.map((a) => (
            <li key={a.action_id} className="border-l-2 border-indigo-300 pl-2 py-0.5">
              <div className="font-mono text-gray-400">{a.payment_id}</div>
              <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
                <span className="text-gray-700 font-medium">{a.action_type || "—"}</span>
                <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                {a.flag_type && (
                  <Badge className={badgeClass(FLAG_STYLES, a.flag_type)}>{a.flag_type}</Badge>
                )}
              </div>
              <div className="text-gray-400 mt-0.5">
                {new Date(a.timestamp * 1000).toLocaleString()} · {a.triggered_by}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
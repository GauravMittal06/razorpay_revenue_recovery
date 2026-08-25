import { useEffect, useState } from "react";
import { fetchCases } from "../api/client";
import FiltersBar from "./FiltersBar";
import { Badge, badgeClass, RECOVERY_STATUS_STYLES, OUTCOME_STYLES } from "../statusColors";
import { LoadingState, ErrorState, EmptyState } from "./LoadingErrorStates";

export default function CaseTable({ onSelectCase, selectedId }) {
  const [filters, setFilters] = useState({
    event_type: "",
    recovery_status: "",
    outcome: "",
  });
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    fetchCases(filters)
      .then((data) => {
        setCases(data);
        setError(null);
      })
      .catch(() => setError("Could not reach /api/cases. Is the backend running?"))
      .finally(() => setLoading(false));
  }, [filters]);

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-gray-900">Cases</h2>
        <span className="text-xs text-gray-400">{cases.length} shown</span>
      </div>

      <div className="mb-3">
        <FiltersBar filters={filters} setFilters={setFilters} />
      </div>

      {error && <ErrorState message={error} />}
      {loading && !error && <LoadingState label="Loading cases…" />}

      {!loading && !error && cases.length === 0 && (
        <EmptyState message="No cases match these filters." />
      )}

      {!loading && !error && cases.length > 0 && (
        <div className="overflow-x-auto max-h-[420px] overflow-y-auto rounded border border-gray-100">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 z-10">
              <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="px-3 py-2">ID</th>
                <th className="px-3 py-2">Customer</th>
                <th className="px-3 py-2">Amount</th>
                <th className="px-3 py-2">Event Type</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Last Action</th>
                <th className="px-3 py-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c, i) => (
                <tr
                  key={c.id}
                  onClick={() => onSelectCase(c.id)}
                  className={`cursor-pointer border-t border-gray-100 transition-colors ${
                    selectedId === c.id
                      ? "bg-indigo-50"
                      : i % 2 === 0
                      ? "bg-white hover:bg-gray-50"
                      : "bg-gray-50/50 hover:bg-gray-50"
                  }`}
                >
                  <td className="px-3 py-2 font-mono text-xs text-gray-500">{c.id}</td>
                  <td className="px-3 py-2 text-gray-800">{c.customer_name || "—"}</td>
                  <td className="px-3 py-2 text-gray-800 font-medium">
                    ₹{c.amount?.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-gray-600">{c.event_type}</td>
                  <td className="px-3 py-2">
                    <Badge className={badgeClass(RECOVERY_STATUS_STYLES, c.recovery_status)}>
                      {c.recovery_status}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-gray-600">{c.action_type || "—"}</td>
                  <td className="px-3 py-2">
                    {c.outcome ? (
                      <Badge className={badgeClass(OUTCOME_STYLES, c.outcome)}>
                        {c.outcome}
                      </Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
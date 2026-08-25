import { useEffect, useState } from "react";
import { fetchCaseDetail } from "../api/client";
import { Badge, badgeClass, RECOVERY_STATUS_STYLES, OUTCOME_STYLES, FLAG_STYLES } from "../statusColors";
import { LoadingState, ErrorState, EmptyState } from "./LoadingErrorStates";

export default function CaseDetail({ paymentId }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!paymentId) return;
    setDetail(null);
    setLoading(true);
    fetchCaseDetail(paymentId)
      .then((data) => {
        setDetail(data);
        setError(null);
      })
      .catch(() => setError("Could not load case detail."))
      .finally(() => setLoading(false));
  }, [paymentId]);

  if (!paymentId) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <EmptyState message="Select a case from the table to view its audit trail." />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <ErrorState message={error} />
      </div>
    );
  }

  if (loading || !detail) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <LoadingState label="Loading case…" />
      </div>
    );
  }

  const { payment, recovery_actions, messages } = detail;

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm divide-y divide-gray-100">
      <div className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-mono text-gray-500">{payment.id}</h2>
          <Badge className={badgeClass(RECOVERY_STATUS_STYLES, payment.recovery_status)}>
            {payment.recovery_status}
          </Badge>
        </div>
        <p className="text-sm text-gray-800 font-medium mt-1">
          {payment.customer_name || "Unknown customer"} · ₹{payment.amount?.toLocaleString()}
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          {payment.event_type}
          {payment.recovered_at
            ? ` · recovered ${new Date(payment.recovered_at * 1000).toLocaleString()}`
            : ""}
        </p>
      </div>

      <div className="p-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Recovery Actions (Audit Trail)
        </h3>
        {recovery_actions.length === 0 ? (
          <EmptyState message="No actions logged yet." />
        ) : (
          <ul className="space-y-3">
            {recovery_actions.map((a) => (
              <li key={a.action_id} className="text-xs border-l-2 border-indigo-300 pl-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-gray-800">{a.action_type || "—"}</span>
                  <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                  {a.flag_type && (
                    <Badge className={badgeClass(FLAG_STYLES, a.flag_type)}>{a.flag_type}</Badge>
                  )}
                </div>
                <div className="text-gray-500 mt-1">{a.reasoning}</div>
                <div className="text-gray-400 mt-1">
                  {new Date(a.timestamp * 1000).toLocaleString()} · {a.triggered_by}
                  {a.ml_recovery_probability != null && ` · ML ${a.ml_recovery_probability}`}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="p-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Messages
        </h3>
        {messages.length === 0 ? (
          <EmptyState message="No messages yet." />
        ) : (
          <ul className="space-y-2">
            {messages.map((m) => (
              <li
                key={m.message_id}
                className={`text-xs p-2 rounded ${
                  m.sender === "agent" ? "bg-indigo-50" : "bg-gray-50"
                }`}
              >
                <div className="font-medium text-gray-700">{m.sender}</div>
                <div className="text-gray-600">{m.content}</div>
                <div className="text-gray-400 mt-0.5">
                  {new Date(m.timestamp * 1000).toLocaleString()}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
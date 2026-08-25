import { useEffect, useState } from "react";
import { fetchCaseDetail } from "../api/client";
import { LoadingState } from "./LoadingErrorStates";

export default function EscalationContextBundle({ paymentId, decision }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!paymentId || decision?.action_type !== "escalate") return;
    setLoading(true);
    fetchCaseDetail(paymentId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [paymentId, decision]);

  if (decision?.action_type !== "escalate") return null;

  if (loading || !detail) {
    return (
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
        <LoadingState label="Loading escalation context…" />
      </div>
    );
  }

  const { payment, recovery_actions, messages } = detail;

  return (
    <div className="bg-amber-50 border border-amber-200 rounded-lg shadow-sm p-4 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-amber-600">⚠</span>
        <h3 className="text-sm font-semibold text-amber-900">Escalation Context Bundle</h3>
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-700 bg-white/60 rounded-md p-3">
        <div><span className="font-medium text-gray-500">Root cause:</span> {payment.error_reason || "—"}</div>
        <div><span className="font-medium text-gray-500">ML probability:</span> {decision.ml_recovery_probability ?? "—"}</div>
        <div><span className="font-medium text-gray-500">Payment history score:</span> {payment.payment_history_score ?? "—"}</div>
        <div><span className="font-medium text-gray-500">Past recovery rate:</span> {payment.past_recovery_rate ?? "—"}</div>
      </div>

      <div>
        <div className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
          Prior actions ({recovery_actions.length})
        </div>
        <ul className="text-xs text-gray-600 space-y-1 bg-white/60 rounded-md p-3">
          {recovery_actions.length === 0 && <li className="text-gray-400">None</li>}
          {recovery_actions.map((a) => (
            <li key={a.action_id}>
              {a.action_type || "—"} · {a.outcome} — {a.reasoning}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <div className="text-xs font-semibold text-gray-600 uppercase tracking-wide mb-1">
          Conversation thread ({messages.length})
        </div>
        <ul className="text-xs text-gray-600 space-y-1 bg-white/60 rounded-md p-3">
          {messages.length === 0 && <li className="text-gray-400">None</li>}
          {messages.map((m) => (
            <li key={m.message_id}>
              <span className="font-medium">{m.sender}:</span> {m.content}
            </li>
          ))}
        </ul>
      </div>

      <div className="text-xs bg-amber-100/60 rounded-md p-3">
        <span className="font-semibold text-amber-900">Recommended next step:</span>{" "}
        <span className="text-gray-700">{decision.action_type} — {decision.reasoning}</span>
      </div>
    </div>
  );
}
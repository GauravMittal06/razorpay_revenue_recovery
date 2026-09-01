import { useEffect, useState } from "react";
import { fetchCaseDetail } from "../api/client";
import { useActiveCase } from "../hooks/useActiveCase";
import { Badge, badgeClass, RECOVERY_STATUS_STYLES, OUTCOME_STYLES, FLAG_STYLES, AuthorityTag } from "../statusColors";
import { LoadingState, ErrorState, EmptyState } from "./LoadingErrorStates";

function SectionLabel({ children }) {
  return <h3 className="eyebrow mb-2">{children}</h3>;
}

export default function CaseDetail() {
  const { activeCaseId } = useActiveCase();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!activeCaseId) return;
    setDetail(null);
    setLoading(true);
    fetchCaseDetail(activeCaseId)
      .then((data) => {
        setDetail(data);
        setError(null);
      })
      .catch(() => setError("Could not load case detail."))
      .finally(() => setLoading(false));
  }, [activeCaseId]);

  if (!activeCaseId) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <EmptyState message="Select a case from the table to view its audit trail." />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <ErrorState message={error} />
      </div>
    );
  }

  if (loading || !detail) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <LoadingState label="Loading case…" />
      </div>
    );
  }

  const { payment, recovery_actions, messages } = detail;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm divide-y divide-[var(--color-line-soft)]">
      <div className="p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-data text-sm text-[var(--color-ink-400)]">{payment.id}</h2>
          <Badge className={badgeClass(RECOVERY_STATUS_STYLES, payment.recovery_status)}>
            {payment.recovery_status}
          </Badge>
        </div>
        <p className="text-sm text-[var(--color-ink-900)] font-medium mt-1">
          {payment.customer_name || "Unknown customer"}{" "}
          <span className="font-data font-normal">₹{payment.amount?.toLocaleString()}</span>
        </p>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5">
          {payment.event_type}
          {payment.recovered_at
            ? ` · recovered ${new Date(payment.recovered_at * 1000).toLocaleString()}`
            : ""}
        </p>
      </div>

      <div className="p-4">
        <SectionLabel>Recovery actions (audit trail)</SectionLabel>
        {recovery_actions.length === 0 ? (
          <EmptyState message="No actions logged yet." />
        ) : (
          <ul className="space-y-3">
            {recovery_actions.map((a) => (
              <li key={a.action_id} className="text-xs border-l-2 border-[var(--color-line)] pl-3">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="font-medium text-[var(--color-ink-800)]">{a.action_type || "—"}</span>
                  <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                  {a.flag_type && (
                    <Badge className={badgeClass(FLAG_STYLES, a.flag_type)}>{a.flag_type}</Badge>
                  )}
                  <AuthorityTag triggeredBy={a.triggered_by} />
                </div>
                <div className="text-[var(--color-ink-500)] mt-1">{a.reasoning}</div>
                <div className="font-data text-[var(--color-ink-300)] mt-1">
                  {new Date(a.timestamp * 1000).toLocaleString()}
                  {a.ml_recovery_probability != null && ` · ML ${a.ml_recovery_probability}`}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="p-4">
        <SectionLabel>Messages</SectionLabel>
        {messages.length === 0 ? (
          <EmptyState message="No messages yet." />
        ) : (
          <ul className="space-y-2">
            {messages.map((m) => (
              <li
                key={m.message_id}
                className={`text-xs p-2 rounded-md ${
                  m.sender === "agent" ? "bg-[var(--color-signal-50)]" : "bg-[var(--color-paper)]"
                }`}
              >
                <div className="font-medium text-[var(--color-ink-700)]">{m.sender}</div>
                <div className="text-[var(--color-ink-600)]">{m.content}</div>
                <div className="font-data text-[var(--color-ink-300)] mt-0.5">
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
import { useEffect, useState } from "react";
import { fetchCaseDetail } from "../api/client";
import { useRecoveryData } from "../hooks/useRecoveryData";
import { useActiveCase } from "../hooks/useActiveCase";
import { Badge, badgeClass, RECOVERY_STATUS_STYLES, OUTCOME_STYLES, AuthorityTag } from "../statusColors";
import { LoadingState, EmptyState } from "./LoadingErrorStates";
import SimulateRecoveryButton from "./SimulateRecoveryButton";

function Section({ label, children }) {
  return (
    <div>
      <div className="eyebrow mb-1.5">{label}</div>
      <div className="bg-[var(--color-paper)] rounded-md p-3">{children}</div>
    </div>
  );
}

// Generalized case context — visible whenever a case is active, not just on
// escalation. The escalation recommendation becomes one conditional block
// inside this panel, rather than the panel's whole reason for existing.
export default function CaseContextPanel() {
  const { lastMutation } = useRecoveryData();
  const { activeCaseId } = useActiveCase();

  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!activeCaseId) {
      setDetail(null);
      return;
    }
    setLoading(true);
    fetchCaseDetail(activeCaseId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
    // Refetch after every mutation too, since a new action may have just
    // been appended to this same case.
  }, [activeCaseId, lastMutation?.at]);

  if (!activeCaseId) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Case context</h3>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-3">
          Trigger an event or send a reply to open a case here.
        </p>
        <EmptyState message="No active case yet." />
      </div>
    );
  }

  if (loading || !detail) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <LoadingState label="Loading case context…" />
      </div>
    );
  }

  const { payment, recovery_actions, messages } = detail;
  const latestAction = recovery_actions[recovery_actions.length - 1];

  // The escalation block only shows when the most recent mutation belongs
  // to *this* active case and actually resulted in an escalate decision —
  // not just whenever any decision anywhere says "escalate".
  const mutationPaymentId =
    lastMutation?.result?.payment?.id || lastMutation?.result?.payment_id || null;
  const decision =
    lastMutation && (lastMutation.kind === "trigger" || lastMutation.kind === "reply")
      ? lastMutation.result?.decision
      : null;
  const showEscalation = decision?.action_type === "escalate" && mutationPaymentId === activeCaseId;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Case context</h3>
        <Badge className={badgeClass(RECOVERY_STATUS_STYLES, payment.recovery_status)}>
          {payment.recovery_status}
        </Badge>
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className="font-data text-[var(--color-ink-400)]">{payment.id}</span>
        <span className="font-data text-[var(--color-ink-900)] font-medium">
          ₹{payment.amount?.toLocaleString()}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Section label="Risk signals">
          <dl className="text-xs text-[var(--color-ink-700)] space-y-1">
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--color-ink-400)]">Root cause</dt>
              <dd className="font-data">{payment.error_reason || "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--color-ink-400)]">Latest ML probability</dt>
              <dd className="font-data">{latestAction?.ml_recovery_probability ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--color-ink-400)]">Payment history score</dt>
              <dd className="font-data">{payment.payment_history_score ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-[var(--color-ink-400)]">Past recovery rate</dt>
              <dd className="font-data">{payment.past_recovery_rate ?? "—"}</dd>
            </div>
          </dl>
        </Section>

        <Section label={`Prior actions (${recovery_actions.length})`}>
          <ul className="text-xs text-[var(--color-ink-600)] space-y-1.5">
            {recovery_actions.length === 0 && <li className="text-[var(--color-ink-300)]">None yet.</li>}
            {recovery_actions.map((a) => (
              <li key={a.action_id} className="flex items-start gap-1.5 flex-wrap">
                <Badge className={badgeClass(OUTCOME_STYLES, a.outcome)}>{a.outcome}</Badge>
                <AuthorityTag triggeredBy={a.triggered_by} />
                <span className="basis-full text-[var(--color-ink-500)]">{a.reasoning}</span>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <Section label={`Conversation thread (${messages.length})`}>
        <ul className="text-xs text-[var(--color-ink-600)] space-y-1">
          {messages.length === 0 && <li className="text-[var(--color-ink-300)]">None yet.</li>}
          {messages.map((m) => (
            <li key={m.message_id}>
              <span className="font-medium">{m.sender}:</span> {m.content}
            </li>
          ))}
        </ul>
      </Section>

      {showEscalation && (
        <div className="bg-amber-50 border border-amber-200 rounded-md p-3 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-amber-900 mb-1">
            <span>⚠</span> Escalation recommended
          </div>
          <span className="text-[var(--color-ink-700)]">{decision.reasoning}</span>
        </div>
      )}

      <SimulateRecoveryButton />
    </div>
  );
}
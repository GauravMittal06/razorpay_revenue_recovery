import { useState } from "react";
import { useRecoveryData } from "../hooks/useRecoveryData";
import { useActiveCase } from "../hooks/useActiveCase";
import { useStagedProgress } from "../hooks/useStagedProgress";
import StagedProgress from "./StagedProgress";
import { ErrorState } from "./LoadingErrorStates";

const EVENT_TYPES = ["checkout_abandoned", "payment_failed", "invoice_overdue"];
const ROOT_CAUSES = [
  "insufficient_funds",
  "payment_declined",
  "gateway_timeout",
  "authentication_failed",
  "expired_card",
  "network_error",
];

const STAGES = ["Creating event", "Classifying", "Evaluating recovery rules", "Compliance / execution", "Audit logged"];
const SUCCESS_LINGER_MS = 1400;

function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="eyebrow">{label}</label>
      {children}
    </div>
  );
}

const inputClass =
  "text-sm border border-[var(--color-line)] rounded-md px-2.5 py-1.5 bg-white text-[var(--color-ink-800)] focus:outline-none focus:ring-2 focus:ring-[var(--color-signal-100)] focus:border-[var(--color-signal-600)] transition-shadow";

export default function EventTriggerForm() {
  const { runTrigger } = useRecoveryData();
  const { setActiveCaseId } = useActiveCase();

  const [eventType, setEventType] = useState("payment_failed");
  const [amount, setAmount] = useState(1000);
  const [rootCause, setRootCause] = useState(ROOT_CAUSES[0]);
  const [daysOverdue, setDaysOverdue] = useState(5);
  const [customerId, setCustomerId] = useState("");
  const [loading, setLoading] = useState(false);
  const [succeeded, setSucceeded] = useState(false);
  const [error, setError] = useState(null);

  const currentStage = useStagedProgress(STAGES.length, loading, succeeded);
  const amountInvalid = Number(amount) <= 0;

  async function handleTrigger() {
    if (amountInvalid) return;
    setLoading(true);
    setSucceeded(false);
    setError(null);
    try {
      const payload = {
        event_type: eventType,
        amount: Number(amount),
        customer_id: customerId || null,
      };
      if (eventType === "payment_failed") payload.root_cause = rootCause;
      if (eventType === "invoice_overdue") payload.days_overdue = Number(daysOverdue);

      const result = await runTrigger(payload);
      if (result.payment?.id) setActiveCaseId(result.payment.id);
      setSucceeded(true);
      setTimeout(() => setSucceeded(false), SUCCESS_LINGER_MS);
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to trigger event.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Event trigger</h3>
      <p className="text-xs text-[var(--color-ink-400)] -mt-2">
        Creates a real payment and runs it through the live recovery pipeline — every field below feeds the reasoning panel.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Event type">
          <select value={eventType} onChange={(e) => setEventType(e.target.value)} className={inputClass} disabled={loading}>
            {EVENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </Field>

        <Field label="Amount (₹)">
          <input
            type="number"
            min="1"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            disabled={loading}
            className={`${inputClass} font-data ${amountInvalid ? "border-red-300" : ""}`}
          />
          {amountInvalid && <span className="text-xs text-red-600">Must be greater than 0</span>}
        </Field>

        {eventType === "payment_failed" && (
          <Field label="Root cause">
            <select value={rootCause} onChange={(e) => setRootCause(e.target.value)} className={inputClass} disabled={loading}>
              {ROOT_CAUSES.map((rc) => (
                <option key={rc} value={rc}>{rc}</option>
              ))}
            </select>
          </Field>
        )}

        {eventType === "invoice_overdue" && (
          <Field label="Days overdue">
            <input
              type="number"
              min="0"
              value={daysOverdue}
              onChange={(e) => setDaysOverdue(e.target.value)}
              disabled={loading}
              className={`${inputClass} font-data`}
            />
          </Field>
        )}

        <Field label="Customer ID (optional)">
          <input
            type="text"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            placeholder="cust_..."
            disabled={loading}
            className={`${inputClass} font-data`}
          />
        </Field>
      </div>

      {error && <ErrorState message={error} />}

      <button
        onClick={handleTrigger}
        disabled={loading || amountInvalid}
        className="text-sm px-4 py-2 rounded-md bg-[var(--color-signal-700)] text-white font-medium hover:bg-[var(--color-signal-900)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "Triggering…" : "Trigger event"}
      </button>

      <StagedProgress stages={STAGES} currentIndex={currentStage} />
    </div>
  );
}
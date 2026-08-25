import { useState } from "react";
import { triggerEvent } from "../api/client";
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

function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-500">{label}</label>
      {children}
    </div>
  );
}

const inputClass =
  "text-sm border border-gray-300 rounded-md px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 transition-shadow";

export default function EventTriggerForm({ onTriggered }) {
  const [eventType, setEventType] = useState("payment_failed");
  const [amount, setAmount] = useState(1000);
  const [rootCause, setRootCause] = useState(ROOT_CAUSES[0]);
  const [daysOverdue, setDaysOverdue] = useState(5);
  const [customerId, setCustomerId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const amountInvalid = Number(amount) <= 0;

  async function handleTrigger() {
    if (amountInvalid) return;
    setLoading(true);
    setError(null);
    try {
      const payload = {
        event_type: eventType,
        amount: Number(amount),
        customer_id: customerId || null,
      };
      if (eventType === "payment_failed") payload.root_cause = rootCause;
      if (eventType === "invoice_overdue") payload.days_overdue = Number(daysOverdue);

      const result = await triggerEvent(payload);
      onTriggered(result);
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to trigger event.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">Event Trigger</h3>
      <p className="text-xs text-gray-400 -mt-2">
        Creates a real payment and runs it through the live recovery pipeline.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Event Type">
          <select value={eventType} onChange={(e) => setEventType(e.target.value)} className={inputClass}>
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
            className={`${inputClass} ${amountInvalid ? "border-red-300" : ""}`}
          />
          {amountInvalid && <span className="text-xs text-red-600">Must be greater than 0</span>}
        </Field>

        {eventType === "payment_failed" && (
          <Field label="Root Cause">
            <select value={rootCause} onChange={(e) => setRootCause(e.target.value)} className={inputClass}>
              {ROOT_CAUSES.map((rc) => (
                <option key={rc} value={rc}>{rc}</option>
              ))}
            </select>
          </Field>
        )}

        {eventType === "invoice_overdue" && (
          <Field label="Days Overdue">
            <input
              type="number"
              min="0"
              value={daysOverdue}
              onChange={(e) => setDaysOverdue(e.target.value)}
              className={inputClass}
            />
          </Field>
        )}

        <Field label="Customer ID (optional)">
          <input
            type="text"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            placeholder="cust_..."
            className={`${inputClass} font-mono`}
          />
        </Field>
      </div>

      {error && <ErrorState message={error} />}

      <button
        onClick={handleTrigger}
        disabled={loading || amountInvalid}
        className="text-sm px-4 py-2 rounded-md bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "Triggering…" : "Trigger Event"}
      </button>
    </div>
  );
}
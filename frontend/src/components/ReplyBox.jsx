import { useState } from "react";
import { submitReply } from "../api/client";
import { ErrorState } from "./LoadingErrorStates";

const inputClass =
  "text-sm border border-gray-300 rounded-md px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 transition-shadow";

export default function ReplyBox({ onReplied, activePaymentId }) {
  const [paymentId, setPaymentId] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const effectiveId = paymentId || activePaymentId || "";

  async function handleSubmit() {
    if (!effectiveId || !message) return;
    setLoading(true);
    setError(null);
    try {
      const result = await submitReply(effectiveId, message);
      onReplied(result);
      setMessage("");
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to submit reply.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">Customer Reply</h3>
      <p className="text-xs text-gray-400 -mt-2">
        Simulates an inbound customer message on an existing case.
      </p>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-gray-500">Payment ID</label>
        <input
          type="text"
          value={paymentId}
          onChange={(e) => setPaymentId(e.target.value)}
          placeholder={activePaymentId || "pay_..."}
          className={`${inputClass} font-mono`}
        />
        {activePaymentId && !paymentId && (
          <span className="text-xs text-gray-400">Using active case: {activePaymentId}</span>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs font-medium text-gray-500">Message</label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Try anything — vague, angry, mixed language, nonsense…"
          rows={3}
          className={inputClass}
        />
      </div>

      {error && <ErrorState message={error} />}

      <button
        onClick={handleSubmit}
        disabled={loading || !effectiveId || !message}
        className="text-sm px-4 py-2 rounded-md bg-indigo-600 text-white font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "Sending…" : "Send Reply"}
      </button>
    </div>
  );
}
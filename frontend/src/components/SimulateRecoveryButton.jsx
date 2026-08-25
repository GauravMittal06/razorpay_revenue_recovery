import { useState } from "react";
import { simulateRecovery } from "../api/client";

export default function SimulateRecoveryButton({ paymentId, onDone }) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  async function handleConfirm() {
    setLoading(true);
    try {
      const res = await simulateRecovery(paymentId);
      setResult(res);
      onDone(res);
    } finally {
      setLoading(false);
      setConfirming(false);
    }
  }

  if (!paymentId) return null;

  return (
    <div className="border-t-2 border-dashed border-gray-300 pt-4">
      <div className="bg-white rounded-lg border border-amber-300 shadow-sm p-4 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-amber-600 text-xs">⚡</span>
          <h3 className="text-sm font-semibold text-gray-900">Simulate Payment Success</h3>
          <span className="text-[10px] uppercase tracking-wide font-semibold text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
            Manual override
          </span>
        </div>
        <p className="text-xs text-gray-500">
          Marks this payment recovered directly for demo purposes — outside the rule engine's decision authority.
        </p>

        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            className="text-sm px-4 py-2 rounded-md border border-amber-400 text-amber-700 font-medium hover:bg-amber-50 transition-colors"
          >
            Simulate Recovery
          </button>
        ) : (
          <div className="flex gap-2 items-center bg-amber-50 rounded-md p-2">
            <span className="text-xs text-gray-700">Confirm for <span className="font-mono">{paymentId}</span>?</span>
            <button
              onClick={handleConfirm}
              disabled={loading}
              className="text-xs px-3 py-1 rounded bg-amber-600 text-white font-medium hover:bg-amber-700 disabled:opacity-50"
            >
              {loading ? "…" : "Confirm"}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="text-xs px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-white"
            >
              Cancel
            </button>
          </div>
        )}

        {result && (
          <div className="text-xs text-gray-500">
            Status: <span className="font-medium">{result.status}</span>
            {result.recovered_at ? ` · recovered_at ${result.recovered_at}` : ""}
          </div>
        )}
      </div>
    </div>
  );
}
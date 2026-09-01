import { useState } from "react";
import { useRecoveryData } from "../hooks/useRecoveryData";
import { useActiveCase } from "../hooks/useActiveCase";
import { useStagedProgress } from "../hooks/useStagedProgress";
import StagedProgress from "./StagedProgress";

const STAGES = ["Updating recovery state", "Metrics / audit updated"];
const SUCCESS_LINGER_MS = 1400;

export default function SimulateRecoveryButton() {
  const { runSimulate } = useRecoveryData();
  const { activeCaseId } = useActiveCase();

  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [succeeded, setSucceeded] = useState(false);
  const [result, setResult] = useState(null);

  const currentStage = useStagedProgress(STAGES.length, loading, succeeded);

  async function handleConfirm() {
    setLoading(true);
    setSucceeded(false);
    try {
      const res = await runSimulate(activeCaseId);
      setResult(res);
      setSucceeded(true);
      setTimeout(() => setSucceeded(false), SUCCESS_LINGER_MS);
    } finally {
      setLoading(false);
      setConfirming(false);
    }
  }

  if (!activeCaseId) return null;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-amber-300 border-dashed shadow-sm p-4 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-amber-600 text-xs">⚡</span>
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Simulate payment success</h3>
        <span className="text-[10px] uppercase tracking-wide font-semibold text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
          Manual override · human, not engine
        </span>
      </div>
      <p className="text-xs text-[var(--color-ink-400)]">
        Marks this payment recovered directly for demo purposes — outside the rule engine's decision authority. This never appears as a rule-engine decision in the audit trail.
      </p>

      {!confirming ? (
        <button
          onClick={() => setConfirming(true)}
          disabled={loading}
          className="text-sm px-4 py-2 rounded-md border border-amber-400 text-amber-700 font-medium hover:bg-amber-50 disabled:opacity-50 transition-colors"
        >
          Simulate recovery
        </button>
      ) : (
        <div className="flex gap-2 items-center bg-amber-50 rounded-md p-2">
          <span className="font-data text-xs text-[var(--color-ink-700)]">
            Confirm for <span className="font-medium">{activeCaseId}</span>?
          </span>
          <button
            onClick={handleConfirm}
            disabled={loading}
            className="text-xs px-3 py-1 rounded bg-amber-600 text-white font-medium hover:bg-amber-700 disabled:opacity-50"
          >
            {loading ? "…" : "Confirm"}
          </button>
          <button
            onClick={() => setConfirming(false)}
            disabled={loading}
            className="text-xs px-3 py-1 rounded border border-[var(--color-line)] text-[var(--color-ink-500)] hover:bg-white disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      )}

      <StagedProgress stages={STAGES} currentIndex={currentStage} />

      {result && currentStage < 0 && (
        <div className="font-data text-xs text-[var(--color-ink-400)]">
          Status: <span className="font-medium">{result.status}</span>
          {result.recovered_at ? ` · recovered_at ${result.recovered_at}` : ""}
        </div>
      )}
    </div>
  );
}
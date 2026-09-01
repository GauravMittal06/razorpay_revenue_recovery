import { useState } from "react";
import { useRecoveryData } from "../hooks/useRecoveryData";
import { useActiveCase } from "../hooks/useActiveCase";
import { useStagedProgress } from "../hooks/useStagedProgress";
import StagedProgress from "./StagedProgress";
import { ErrorState } from "./LoadingErrorStates";

const STAGES = ["Understanding customer message", "Rule engine evaluating", "Compliance / execution", "Audit updated"];
const SUCCESS_LINGER_MS = 1400;

// Quick-fill presets mirroring the SoT's own locked demo scenarios (§9d) —
// these are sanctioned test inputs for exercising the real LLM/rule-engine
// pipeline (confidence gating, mismatch detection, language switching), not
// fabricated business data. Purely a textarea convenience; every message
// still goes through the real runReply() -> LLM -> rule engine.
const PRESETS = [
  { label: "Hinglish promise-to-pay", msg: "kal pay kar dunga bhai, salary aa rahi hai" },
  { label: "Technical issue", msg: "the OTP page is not working, tried many times error aa raha hai" },
  { label: "Dispute", msg: "I never authorized this charge, please stop contacting me" },
  { label: "Ambiguous", msg: "I'll try" },
  { label: "Payment method updated", msg: "I've updated my card, please try again" },
];

const inputClass =
  "text-sm border border-[var(--color-line)] rounded-md px-2.5 py-1.5 bg-white text-[var(--color-ink-800)] focus:outline-none focus:ring-2 focus:ring-[var(--color-signal-100)] focus:border-[var(--color-signal-600)] transition-shadow";

export default function ReplyBox() {
  const { runReply } = useRecoveryData();
  const { activeCaseId, setActiveCaseId } = useActiveCase();

  const [paymentId, setPaymentId] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [succeeded, setSucceeded] = useState(false);
  const [error, setError] = useState(null);

  const currentStage = useStagedProgress(STAGES.length, loading, succeeded);
  const effectiveId = paymentId || activeCaseId || "";

  async function handleSubmit() {
    if (!effectiveId || !message) return;
    setLoading(true);
    setSucceeded(false);
    setError(null);
    try {
      const result = await runReply(effectiveId, message);
      if (result.payment_id) setActiveCaseId(result.payment_id);
      setMessage("");
      setSucceeded(true);
      setTimeout(() => setSucceeded(false), SUCCESS_LINGER_MS);
    } catch (e) {
      setError(e.response?.data?.detail || "Failed to submit reply.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4 space-y-3">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Customer reply</h3>
      <p className="text-xs text-[var(--color-ink-400)] -mt-2">
        Simulates an inbound customer message on an existing case — this is the only input that actually reaches the LLM.
      </p>

      <div className="flex flex-col gap-1">
        <label className="eyebrow">Payment ID</label>
        <input
          type="text"
          value={paymentId}
          onChange={(e) => setPaymentId(e.target.value)}
          placeholder={activeCaseId || "pay_..."}
          disabled={loading}
          className={`${inputClass} font-data`}
        />
        {activeCaseId && !paymentId && (
          <span className="text-xs text-[var(--color-ink-400)]">Using active case: {activeCaseId}</span>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <label className="eyebrow">Message</label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Try anything — vague, angry, mixed language, nonsense…"
          rows={3}
          disabled={loading}
          className={inputClass}
        />
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => setMessage(p.msg)}
              disabled={loading}
              className="font-mono text-[10px] uppercase tracking-wide rounded-full border border-[var(--color-line)] bg-[var(--color-paper)] px-2.5 py-1 text-[var(--color-ink-500)] hover:border-[var(--color-signal-600)] hover:text-[var(--color-signal-700)] transition-colors disabled:opacity-40"
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && <ErrorState message={error} />}

      <button
        onClick={handleSubmit}
        disabled={loading || !effectiveId || !message}
        className="text-sm px-4 py-2 rounded-md bg-[var(--color-signal-700)] text-white font-medium hover:bg-[var(--color-signal-900)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? "Sending…" : "Send reply"}
      </button>

      <StagedProgress stages={STAGES} currentIndex={currentStage} />
    </div>
  );
}
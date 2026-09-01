import { useEffect, useRef, useState } from "react";
import { useRecoveryData } from "../hooks/useRecoveryData";
import { Badge, badgeClass, OUTCOME_STYLES, FLAG_STYLES } from "../statusColors";
import { EmptyState } from "./LoadingErrorStates";

const STAGE_GAP_MS = 380;

function Connector({ label }) {
  return (
    <div className="flex items-center gap-2 pl-[10px] py-1">
      <div className="w-px h-4 bg-[var(--color-line)]" />
      <span className="text-[11px] text-[var(--color-ink-400)] italic">{label}</span>
    </div>
  );
}

function StageHeader({ step, title, tone }) {
  const dot =
    tone === "advisory"
      ? "bg-slate-400"
      : tone === "authoritative"
      ? "bg-[var(--color-signal-600)]"
      : "bg-green-600";
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className={`flex items-center justify-center w-5 h-5 rounded-full text-white text-[10px] font-bold ${dot}`}>
        {step}
      </span>
      <span className="text-xs font-semibold text-[var(--color-ink-700)] uppercase tracking-wide">{title}</span>
      {tone === "advisory" && (
        <span className="text-[10px] uppercase tracking-wide font-semibold text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
          advisory
        </span>
      )}
      {tone === "authoritative" && (
        <span className="text-[10px] uppercase tracking-wide font-semibold text-[var(--color-signal-700)] bg-[var(--color-signal-50)] px-1.5 py-0.5 rounded">
          authoritative
        </span>
      )}
      {tone === "final" && <span className="text-green-600 text-xs">✓ final</span>}
    </div>
  );
}

export default function ReasoningPanel() {
  const { lastMutation } = useRecoveryData();

  // Only trigger/reply produce a parsed_intent → decision → execution shape.
  // Simulate-recovery is a manual override outside the engine's authority
  // and is represented separately (SimulateRecoveryButton), never here.
  const causal = lastMutation && (lastMutation.kind === "trigger" || lastMutation.kind === "reply")
    ? lastMutation.result
    : null;

  const [visibleStages, setVisibleStages] = useState(causal ? 3 : 0);
  const lastRevealedAtRef = useRef(null);
  const timeoutsRef = useRef([]);

  useEffect(() => {
    if (!lastMutation || !causal) return;

    // Only replay the reveal animation for a genuinely new mutation. If this
    // is the same mutation we already revealed (e.g. a re-render triggered
    // by polling), just show the complete final state immediately.
    if (lastRevealedAtRef.current === lastMutation.at) {
      setVisibleStages(3);
      return;
    }
    lastRevealedAtRef.current = lastMutation.at;

    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];

    setVisibleStages(causal.parsed_intent ? 1 : 0);
    if (causal.decision) {
      timeoutsRef.current.push(setTimeout(() => setVisibleStages(2), STAGE_GAP_MS));
    }
    if (causal.execution_result) {
      timeoutsRef.current.push(setTimeout(() => setVisibleStages(3), STAGE_GAP_MS * 2));
    }

    return () => timeoutsRef.current.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastMutation?.at]);

  if (!causal) {
    return (
      <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
        <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Agent reasoning</h3>
        <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-3">
          LLM only extracts intent. The rule engine alone decides and executes.
        </p>
        <EmptyState message="Trigger an event or submit a reply to see live reasoning here." />
      </div>
    );
  }

  const { parsed_intent: parsedIntent, decision, execution_result: execution } = causal;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4 space-y-1">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Agent reasoning</h3>
      <p className="text-xs text-[var(--color-ink-400)] -mt-0.5 mb-3">
        LLM only extracts intent. The rule engine alone decides and executes.
      </p>

      {parsedIntent && visibleStages >= 1 && (
        <div className="stage-reveal border border-slate-200 bg-slate-50/60 rounded-lg p-3">
          <StageHeader step="1" title="LLM output — language only" tone="advisory" />
          <pre className="text-xs bg-white border border-slate-200 rounded p-2 overflow-x-auto font-data text-[var(--color-ink-700)]">
            {JSON.stringify(parsedIntent, null, 2)}
          </pre>
        </div>
      )}

      {decision && visibleStages >= 1 && parsedIntent && (
        <Connector label="Rule engine evaluates this output — it has no authority on its own" />
      )}

      {decision && visibleStages >= 2 && (
        <div className="stage-reveal border border-[var(--color-signal-100)] bg-[var(--color-signal-50)]/70 rounded-lg p-3">
          <StageHeader step="2" title="Rule engine decision" tone="authoritative" />
          <div className="text-xs bg-white border border-[var(--color-signal-100)] rounded p-2 space-y-1.5">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-[var(--color-ink-500)]">action:</span>
              <span className="font-data text-[var(--color-ink-800)]">{decision.action_type || "—"}</span>
              <Badge className={badgeClass(OUTCOME_STYLES, decision.outcome)}>{decision.outcome}</Badge>
              {decision.flag_type && (
                <Badge className={badgeClass(FLAG_STYLES, decision.flag_type)}>{decision.flag_type}</Badge>
              )}
            </div>
            <div className="text-[var(--color-ink-600)]">{decision.reasoning}</div>
            {decision.ml_recovery_probability != null && (
              <div className="font-data text-[var(--color-ink-400)]">
                ML recovery probability: <span className="font-medium">{decision.ml_recovery_probability}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {execution && visibleStages >= 2 && (
        <Connector label="Decision is executed — this outcome is final and audited" />
      )}

      {execution && visibleStages >= 3 && (
        <div className="stage-reveal border border-green-100 bg-green-50/60 rounded-lg p-3">
          <StageHeader step="3" title="Compliance / execution" tone="final" />
          <div className="text-xs bg-white border border-green-100 rounded p-2 space-y-1">
            <div>
              <span className="font-medium text-[var(--color-ink-500)]">payment_id:</span>{" "}
              <span className="font-data text-[var(--color-ink-700)]">{execution.payment_id}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-[var(--color-ink-500)]">outcome:</span>
              <Badge className={badgeClass(OUTCOME_STYLES, execution.outcome)}>{execution.outcome}</Badge>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
import { Badge, badgeClass, OUTCOME_STYLES, FLAG_STYLES } from "../statusColors";
import { EmptyState } from "./LoadingErrorStates";

function StageHeader({ step, title, icon }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="flex items-center justify-center w-5 h-5 rounded-full bg-gray-900 text-white text-[10px] font-bold">
        {step}
      </span>
      <span className="text-xs font-semibold text-gray-700 uppercase tracking-wide">{title}</span>
      {icon}
    </div>
  );
}

export default function ReasoningPanel({ result }) {
  if (!result) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4">
        <EmptyState message="Trigger an event or submit a reply to see live reasoning here." />
      </div>
    );
  }

  const parsedIntent = result.parsed_intent;
  const decision = result.decision;
  const execution = result.execution_result;

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm p-4 space-y-4">
      <h3 className="text-sm font-semibold text-gray-900">Agent Reasoning</h3>
      <p className="text-xs text-gray-400 -mt-2">
        LLM only extracts intent. The rule engine alone decides and executes.
      </p>

      {parsedIntent && (
        <div className="border border-blue-100 bg-blue-50/40 rounded-lg p-3">
          <StageHeader step="1" title="LLM Output (language only, no authority)" />
          <pre className="text-xs bg-white border border-blue-100 rounded p-2 overflow-x-auto font-mono text-gray-700">
            {JSON.stringify(parsedIntent, null, 2)}
          </pre>
        </div>
      )}

      {decision && (
        <div className="border border-indigo-100 bg-indigo-50/40 rounded-lg p-3">
          <StageHeader step="2" title="Rule Engine Decision" />
          <div className="text-xs bg-white border border-indigo-100 rounded p-2 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="font-medium text-gray-500">action:</span>
              <span className="text-gray-800">{decision.action_type || "—"}</span>
              <Badge className={badgeClass(OUTCOME_STYLES, decision.outcome)}>
                {decision.outcome}
              </Badge>
              {decision.flag_type && (
                <Badge className={badgeClass(FLAG_STYLES, decision.flag_type)}>
                  {decision.flag_type}
                </Badge>
              )}
            </div>
            <div className="text-gray-600">{decision.reasoning}</div>
            {decision.ml_recovery_probability != null && (
              <div className="text-gray-500">
                ML recovery probability: <span className="font-medium">{decision.ml_recovery_probability}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {execution && (
        <div className="border border-green-100 bg-green-50/40 rounded-lg p-3">
          <StageHeader
            step="3"
            title="Compliance / Execution (final, authoritative)"
            icon={<span className="text-green-600 text-xs">✓</span>}
          />
          <div className="text-xs bg-white border border-green-100 rounded p-2 space-y-1">
            <div>
              <span className="font-medium text-gray-500">payment_id:</span>{" "}
              <span className="font-mono text-gray-700">{execution.payment_id}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-gray-500">outcome:</span>
              <Badge className={badgeClass(OUTCOME_STYLES, execution.outcome)}>
                {execution.outcome}
              </Badge>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
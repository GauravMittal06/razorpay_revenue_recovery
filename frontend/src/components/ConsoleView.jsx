import { useState } from "react";
import EventTriggerForm from "./EventTriggerForm";
import ReplyBox from "./ReplyBox";
import ReasoningPanel from "./ReasoningPanel";
import EscalationContextBundle from "./EscalationContextBundle";
import SimulateRecoveryButton from "./SimulateRecoveryButton";
import LiveAuditFeed from "./LiveAuditFeed";
import MetricsPanel from "./MetricsPanel";

function ActiveCaseIndicator({ paymentId }) {
  if (!paymentId) return null;
  return (
    <div className="flex items-center gap-2 bg-indigo-50 border border-indigo-200 rounded-full px-3 py-1.5 w-fit">
      <span className="w-1.5 h-1.5 rounded-full bg-indigo-500" />
      <span className="text-xs text-indigo-700">
        Active case: <span className="font-mono font-medium">{paymentId}</span>
      </span>
    </div>
  );
}

export default function ConsoleView() {
  const [lastResult, setLastResult] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [activePaymentId, setActivePaymentId] = useState(null);

  function handleTriggered(result) {
    setLastResult(result);
    setActivePaymentId(result.payment?.id || null);
    setRefreshKey((k) => k + 1);
  }

  function handleReplied(result) {
    setLastResult(result);
    setActivePaymentId(result.payment_id || null);
    setRefreshKey((k) => k + 1);
  }

  function handleSimulated() {
    setRefreshKey((k) => k + 1);
  }

  return (
    <div className="space-y-6">
      <MetricsPanel key={refreshKey} />

      <ActiveCaseIndicator paymentId={activePaymentId} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <EventTriggerForm onTriggered={handleTriggered} />
        <ReplyBox onReplied={handleReplied} activePaymentId={activePaymentId} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ReasoningPanel result={lastResult} />
        <LiveAuditFeed refreshKey={refreshKey} />
      </div>

      <EscalationContextBundle paymentId={activePaymentId} decision={lastResult?.decision} />

      <SimulateRecoveryButton paymentId={activePaymentId} onDone={handleSimulated} />
    </div>
  );
}
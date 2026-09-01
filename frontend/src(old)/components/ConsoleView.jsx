import EventTriggerForm from "./EventTriggerForm";
import ReplyBox from "./ReplyBox";
import ReasoningPanel from "./ReasoningPanel";
import CaseContextPanel from "./CaseContextPanel";
import { useActiveCase } from "../hooks/useActiveCase";

function ActiveCaseIndicator() {
  const { activeCaseId } = useActiveCase();
  if (!activeCaseId) {
    return (
      <div className="text-xs text-[var(--color-ink-400)] px-1">
        No active case yet — trigger an event or send a reply to begin investigating.
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2 bg-[var(--color-signal-50)] border border-[var(--color-signal-100)] rounded-full px-3 py-1.5 w-fit">
      <span className="w-1.5 h-1.5 rounded-full bg-[var(--color-signal-600)]" />
      <span className="text-xs text-[var(--color-signal-700)]">
        Active case: <span className="font-data font-medium">{activeCaseId}</span>
      </span>
    </div>
  );
}

// Live Agent view body — the one-case live process view. Restored priority
// order per the approved workflow: Event Trigger + Customer Reply → AI
// Understanding → Rule Engine Decision → Compliance/Execution → Case
// Context. LiveAuditFeed lives exclusively in the Audit Trail view now, not
// here — this view is scoped to one case, Audit Trail is the global ledger.
export default function ConsoleView() {
  return (
    <div className="space-y-6">
      <ActiveCaseIndicator />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <EventTriggerForm />
        <ReplyBox />
      </div>

      <ReasoningPanel />

      <CaseContextPanel />
    </div>
  );
}
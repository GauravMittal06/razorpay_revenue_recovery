// Shared presentation constants — recovery_status / outcome / flag_type /
// triggered_by color tokens, used consistently across CaseTable, CaseDetail,
// LiveAuditFeed, ReasoningPanel, CaseContextPanel. Presentation only,
// no data semantics — every value here maps 1:1 to a value the backend
// already returns.

export const RECOVERY_STATUS_STYLES = {
  open: "bg-gray-100 text-gray-700 border-gray-300",
  recovering: "bg-blue-50 text-blue-700 border-blue-300",
  escalated: "bg-amber-50 text-amber-700 border-amber-300",
  stopped: "bg-red-50 text-red-700 border-red-300",
  recovered: "bg-green-50 text-green-700 border-green-300",
};

export const OUTCOME_STYLES = {
  executed: "bg-green-50 text-green-700 border-green-300",
  blocked_contact_hours: "bg-gray-100 text-gray-600 border-gray-300",
  blocked_cooldown: "bg-gray-100 text-gray-600 border-gray-300",
  blocked_already_stopped: "bg-gray-100 text-gray-600 border-gray-300",
  blocked_already_escalated: "bg-gray-100 text-gray-600 border-gray-300",
  flagged_manual_review: "bg-amber-50 text-amber-700 border-amber-300",
};

export const FLAG_STYLES = {
  mismatch: "bg-red-50 text-red-700 border-red-300",
  root_cause_update_candidate: "bg-blue-50 text-blue-700 border-blue-300",
  dispute_flag: "bg-red-50 text-red-700 border-red-300",
};

// triggered_by: distinguishes engine-decided activity (rule / ml / llm) from
// human/manual activity, per the SoT's authority hierarchy. "rule" is the
// only genuinely *authoritative* source — ml and llm are advisory inputs
// the rule engine consumes, so they share a quieter, non-authoritative tone
// while "rule" gets the signal-accent treatment. "manual" is visually
// unmistakable (dashed border) since it is an external override, not an
// engine decision.
export const TRIGGERED_BY_STYLES = {
  rule: "bg-[var(--color-signal-50)] text-[var(--color-signal-700)] border-[var(--color-signal-600)]",
  ml: "bg-slate-50 text-slate-600 border-slate-300",
  llm: "bg-slate-50 text-slate-600 border-slate-300",
  manual: "bg-amber-50 text-amber-700 border-amber-400 border-dashed",
  system: "bg-[var(--color-signal-50)] text-[var(--color-signal-700)] border-[var(--color-signal-600)]",
};

export const TRIGGERED_BY_LABELS = {
  rule: "Rule engine",
  ml: "ML model",
  llm: "LLM (advisory)",
  manual: "Manual",
  system: "System",
};

export function badgeClass(map, key, fallback = "bg-gray-100 text-gray-600 border-gray-300") {
  return map[key] || fallback;
}

export function Badge({ children, className = "" }) {
  return (
    <span
      className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full border ${className}`}
    >
      {children}
    </span>
  );
}

// A slightly heavier badge used specifically for "who/what acted" — a small
// dot + label, so authority is legible at a glance even before reading text.
export function AuthorityTag({ triggeredBy }) {
  if (!triggeredBy) return null;
  const isManual = triggeredBy === "manual";
  const isAuthoritative = triggeredBy === "rule" || triggeredBy === "system";
  const dotColor = isManual
    ? "bg-amber-500"
    : isAuthoritative
    ? "bg-[var(--color-signal-600)]"
    : "bg-slate-400";

  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11px] font-medium px-2 py-0.5 rounded-full border ${badgeClass(
        TRIGGERED_BY_STYLES,
        triggeredBy
      )}`}
      title={
        isManual
          ? "Human-initiated action, outside engine authority"
          : isAuthoritative
          ? "Authoritative — decided and executed by the rule engine"
          : "Advisory input only — not a decision"
      }
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      {TRIGGERED_BY_LABELS[triggeredBy] || triggeredBy}
    </span>
  );
}
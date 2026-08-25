// Shared presentation constants — recovery_status / outcome / flag_type
// color tokens, used consistently across CaseTable, CaseDetail,
// LiveAuditFeed, ReasoningPanel. Presentation only, no data semantics.

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
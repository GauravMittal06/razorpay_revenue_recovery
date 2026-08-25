const EVENT_TYPES = ["", "checkout_abandoned", "payment_failed", "invoice_overdue"];
const RECOVERY_STATUSES = ["", "open", "recovering", "escalated", "stopped", "recovered"];
const OUTCOMES = [
  "",
  "executed",
  "blocked_contact_hours",
  "blocked_cooldown",
  "blocked_already_stopped",
  "blocked_already_escalated",
  "flagged_manual_review",
];

function Select({ label, value, options, onChange }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-gray-500">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt === "" ? "All" : opt}
          </option>
        ))}
      </select>
    </div>
  );
}

export default function FiltersBar({ filters, setFilters }) {
  return (
    <div className="flex gap-4 bg-white border border-gray-200 rounded-lg p-3">
      <Select
        label="Event Type"
        value={filters.event_type}
        options={EVENT_TYPES}
        onChange={(v) => setFilters({ ...filters, event_type: v })}
      />
      <Select
        label="Recovery Status"
        value={filters.recovery_status}
        options={RECOVERY_STATUSES}
        onChange={(v) => setFilters({ ...filters, recovery_status: v })}
      />
      <Select
        label="Outcome"
        value={filters.outcome}
        options={OUTCOMES}
        onChange={(v) => setFilters({ ...filters, outcome: v })}
      />
    </div>
  );
}
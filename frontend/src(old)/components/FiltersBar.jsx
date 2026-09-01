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
      <label className="eyebrow">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-sm border border-[var(--color-line)] rounded-md px-2 py-1.5 bg-white text-[var(--color-ink-700)] focus:outline-none focus:ring-2 focus:ring-[var(--color-signal-100)] focus:border-[var(--color-signal-600)] transition-shadow"
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
    <div className="flex flex-wrap gap-4 bg-[var(--color-paper)] border border-[var(--color-line)] rounded-lg p-3">
      <Select
        label="Event type"
        value={filters.event_type}
        options={EVENT_TYPES}
        onChange={(v) => setFilters({ ...filters, event_type: v })}
      />
      <Select
        label="Recovery status"
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
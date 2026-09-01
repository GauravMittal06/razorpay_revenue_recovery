import { useRecoveryData } from "../hooks/useRecoveryData";
import { useViewNav } from "../hooks/useViewNav";

// Small hand-written stroke icons (16px, feather-style) — kept local so the
// sidebar doesn't pull in a new icon library dependency the rest of the app
// doesn't already use.
function Icon({ path, size = 16 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {path}
    </svg>
  );
}

const icons = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>
  ),
  inbox: (
    <>
      <path d="M3 12h4.5l1.5 3h6l1.5-3H21" />
      <path d="M5 12 3.5 5.5A1 1 0 0 1 4.5 4h15a1 1 0 0 1 1 1.5L19 12" />
      <path d="M3 12v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6" />
    </>
  ),
  activity: (
    <path d="M3 12h4l2.5-7L14 19l2.5-7H21" />
  ),
  bar: (
    <>
      <line x1="6" y1="20" x2="6" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="18" y1="20" x2="18" y2="14" />
    </>
  ),
  fileCheck: (
    <>
      <path d="M14 3v5a1 1 0 0 0 1 1h5" />
      <path d="M6 3h8l6 6v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="m9 14 2 2 4-4" />
    </>
  ),
  shield: (
    <path d="M12 3 4.5 6v6c0 4.5 3.2 7.6 7.5 9 4.3-1.4 7.5-4.5 7.5-9V6L12 3Z" />
  ),
  alert: (
    <>
      <path d="M12 3 2 20h20L12 3Z" />
      <line x1="12" y1="10" x2="12" y2="14.5" />
      <circle cx="12" cy="17.3" r="0.4" fill="currentColor" />
    </>
  ),
  pulse: (
    <circle cx="12" cy="12" r="4" />
  ),
};

const NAV_SECTIONS = [
  {
    label: "Primary",
    items: [
      { id: "overview", label: "Overview", icon: "overview" },
      { id: "recovery-queue", label: "Recovery queue", icon: "inbox", badgeKey: "openCases" },
      { id: "live-agent", label: "Live agent", icon: "activity", live: true },
    ],
  },
  {
    label: "Insights",
    items: [
      { id: "analytics", label: "Recovery analytics", icon: "bar" },
      { id: "audit-trail", label: "Audit trail", icon: "fileCheck" },
    ],
  },
  {
    label: "Controls",
    items: [
      { id: "policies", label: "Recovery policies", icon: "shield", disabled: true },
      { id: "exceptions", label: "Exceptions", icon: "alert", badgeKey: "exceptions" },
    ],
  },
];

function NavRow({ item, active, onClick, badge }) {
  if (item.disabled) {
    return (
      <div
        className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[var(--color-ink-300)] cursor-not-allowed select-none"
        title="Not implemented in this build — rule definitions live in the backend engine, not yet exposed as a UI."
      >
        <Icon path={icons[item.icon]} />
        <span className="text-[13px] font-medium flex-1">{item.label}</span>
        <span className="text-[9px] font-semibold uppercase tracking-wide text-[var(--color-ink-300)] bg-[var(--color-paper)] border border-[var(--color-line)] rounded px-1.5 py-0.5">
          Soon
        </span>
      </div>
    );
  }

  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors ${
        active
          ? "bg-[var(--color-signal-50)] text-[var(--color-signal-700)]"
          : "text-[var(--color-ink-500)] hover:bg-[var(--color-paper)] hover:text-[var(--color-ink-800)]"
      }`}
    >
      <span className="relative">
        <Icon path={icons[item.icon]} />
        {item.live && (
          <span className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
        )}
      </span>
      <span className="text-[13px] font-medium flex-1">{item.label}</span>
      {badge != null && badge > 0 && (
        <span
          className={`text-[10px] font-semibold rounded-full px-1.5 py-0.5 ${
            item.id === "exceptions"
              ? "bg-amber-50 text-amber-700"
              : "bg-[var(--color-line-soft)] text-[var(--color-ink-500)]"
          }`}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

export default function Sidebar() {
  const { cases, metrics, metricsError } = useRecoveryData();
  const { activeView, navigateTo } = useViewNav();

  const openCases = cases.filter((c) => c.recovery_status === "open" || c.recovery_status === "escalated").length;
  const exceptions = metrics?.unresolved_exceptions_count ?? 0;

  const badgeValues = { openCases, exceptions };

  return (
    <aside className="w-60 shrink-0 bg-[var(--color-surface)] border-r border-[var(--color-line)] flex flex-col h-screen sticky top-0">
      <div className="flex items-center gap-2.5 px-4 pt-5 pb-4">
        <div className="w-7 h-7 rounded-md bg-[var(--color-signal-50)] text-[var(--color-signal-700)] flex items-center justify-center">
          <Icon path={icons.activity} size={15} />
        </div>
        <div className="leading-tight">
          <div className="text-[14px] font-bold text-[var(--color-ink-900)] tracking-tight">Revenue Recovery</div>
          <div className="text-[10px] text-[var(--color-ink-400)]">Control tower</div>
        </div>
      </div>

      <nav className="flex-1 px-2.5 overflow-y-auto">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="mb-1">
            <div className="eyebrow px-2.5 pt-3 pb-1.5">{section.label}</div>
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavRow
                  key={item.id}
                  item={item}
                  active={activeView === item.id}
                  onClick={() => navigateTo(item.id)}
                  badge={item.badgeKey ? badgeValues[item.badgeKey] : null}
                />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="mt-auto px-4 py-4 border-t border-[var(--color-line)]">
        <div className="flex items-center gap-2 text-xs">
          <span className={`w-1.5 h-1.5 rounded-full ${metricsError ? "bg-red-500" : "bg-green-500"}`} />
          <span className={metricsError ? "text-red-600" : "text-[var(--color-ink-500)]"}>
            {metricsError ? "Connection issue" : "Recovery engine online"}
          </span>
        </div>
      </div>
    </aside>
  );
}
import { useState } from "react";
import { EmptyState } from "./LoadingErrorStates";

// Root cause -> human label. Presentation only — the underlying keys are
// exactly the 6 locked error_reason values from the SoT; no 7th cause is
// invented and nothing here is fetched separately, this reads the same
// metrics.recovery_by_root_cause object RootCauseChart/RootCauseTable use.
const LABELS = {
  insufficient_funds: "Insufficient funds",
  payment_declined: "Payment declined",
  gateway_timeout: "Gateway timeout",
  authentication_failed: "Authentication failed",
  expired_card: "Expired card",
  network_error: "Network error",
};

// Fixed display order matching the SoT's locked root-cause list (§6a),
// not an editorializing "ranked by share" order like the V0 reference —
// we don't want to imply an unverified frequency ranking here since it
// depends entirely on the live 150-record dataset's actual distribution.
const ORDER = [
  "insufficient_funds",
  "payment_declined",
  "gateway_timeout",
  "authentication_failed",
  "expired_card",
  "network_error",
];

export default function RootCauseAccordion({ recoveryByRootCause }) {
  const [open, setOpen] = useState(ORDER[0]);

  if (!recoveryByRootCause) return null;

  const totalAcrossCauses = ORDER.reduce((sum, k) => sum + (recoveryByRootCause[k]?.total || 0), 0);
  const hasAnyData = totalAcrossCauses > 0;

  return (
    <div className="bg-[var(--color-surface)] rounded-lg border border-[var(--color-line)] shadow-sm p-4">
      <h3 className="text-sm font-semibold text-[var(--color-ink-900)]">Recovery by root cause</h3>
      <p className="text-xs text-[var(--color-ink-400)] mt-0.5 mb-3">
        The 6 locked failure categories, and how the engine is actually recovering from each — live from the
        current dataset, not a fixed ranking.
      </p>

      {!hasAnyData ? (
        <EmptyState message="No payment_failed cases recorded yet." />
      ) : (
        <div className="border-t border-[var(--color-line-soft)]">
          {ORDER.map((cause, i) => {
            const v = recoveryByRootCause[cause] || { recovered: 0, total: 0, recovery_rate_pct: 0 };
            const isOpen = open === cause;
            const sharePct = totalAcrossCauses > 0 ? Math.round((v.total / totalAcrossCauses) * 100) : 0;
            return (
              <button
                key={cause}
                type="button"
                onClick={() => setOpen(cause)}
                onMouseEnter={() => setOpen(cause)}
                aria-expanded={isOpen}
                className="group grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 border-b border-[var(--color-line-soft)] py-3.5 text-left transition-colors sm:gap-6"
              >
                <span className="font-mono text-[11px] text-[var(--color-ink-400)]">
                  {String(i + 1).padStart(2, "0")}
                </span>

                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
                    <span
                      className={`font-serif text-base transition-colors sm:text-lg ${
                        isOpen ? "text-[var(--color-signal-600)]" : "text-[var(--color-ink-900)]"
                      }`}
                    >
                      {LABELS[cause]}
                    </span>
                    <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-ink-400)]">
                      {v.recovered}/{v.total} cases
                    </span>
                  </div>
                  <div
                    className={`grid transition-all duration-300 ease-out ${
                      isOpen ? "mt-1.5 grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
                    }`}
                  >
                    <p className="overflow-hidden text-xs leading-relaxed text-[var(--color-ink-400)]">
                      {v.total === 0
                        ? "No cases recorded for this root cause yet."
                        : `${sharePct}% of all payment_failed cases in the current dataset.`}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-4 sm:gap-8">
                  <div className="hidden text-right sm:block">
                    <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--color-ink-400)]">Share</p>
                    <p className="font-serif text-base text-[var(--color-ink-900)]">{sharePct}%</p>
                  </div>
                  <div className="w-20 text-right sm:w-28">
                    <p className="font-mono text-[9px] uppercase tracking-[0.14em] text-[var(--color-ink-400)]">Recovery</p>
                    <div className="mt-1 flex items-center gap-1.5">
                      <span className="relative h-1 flex-1 overflow-hidden rounded-full bg-[var(--color-line)]">
                        <span
                          className="absolute inset-y-0 left-0 rounded-full bg-green-500 transition-[width] duration-500"
                          style={{ width: isOpen ? `${v.recovery_rate_pct}%` : "0%" }}
                        />
                      </span>
                      <span className="font-serif text-base tabular-nums text-[var(--color-ink-900)]">
                        {v.recovery_rate_pct}%
                      </span>
                    </div>
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

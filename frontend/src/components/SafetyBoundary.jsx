import Reveal from "./Reveal";

// Static explainer — the architecture claim ("AI advises, rules decide,
// compliance controls execution") stated in plain language, plus the real
// locked compliance constants from SoT §7/§7a/§7b. Nothing here is fetched
// (these are fixed rule-engine constants, not live data) but every value is
// copied verbatim from the SoT — none invented or approximated. There is no
// ₹-amount manual-review gate in the real system; the real manual-review
// trigger is the LLM confidence threshold plus mismatch/dispute flags, so
// that is what's shown here instead of a fabricated amount threshold.
const CAN = [
  "Read customer messages and infer intent, confidence and mentioned reason",
  "Extract structured signals — promise-to-pay, dispute, payment-method-updated",
  "Recommend which rule-engine action best fits, as an advisory signal only",
  "Score recovery probability (ML) to inform the rule engine's decision",
];

const CANNOT = [
  "Move, capture, refund or retry money on its own",
  "Override a compliance gate or a stopping rule",
  "Contact a customer outside the permitted 9am–8pm window",
  "Select, trigger or override a recovery action — only the rule engine can",
];

const GATES = [
  { label: "Contact hours", value: "9am – 8pm", note: "Retry/reminder messages outside this window are blocked, not queued." },
  { label: "Retry ceiling", value: "3 max", note: "MAX_RETRIES — no fourth automated retry attempt." },
  { label: "Cooldown", value: "24 h", note: "COOLDOWN_HOURS — minimum gap between contact attempts." },
  { label: "Auto-escalate", value: "7 days", note: "AUTO_STOP_DAYS — no response routes to the human queue." },
  { label: "Confidence gate", value: "< 0.6", note: "Below CONFIDENCE_THRESHOLD, the engine flags for manual review instead of acting." },
];

export default function SafetyBoundary() {
  return (
    <Reveal>
      <section className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-5 sm:p-7">
        <p className="eyebrow">Architecture, not a setting</p>
        <h2 className="mt-3 max-w-2xl text-balance font-serif text-2xl font-light leading-tight tracking-[-0.01em] text-[var(--color-ink-900)] sm:text-3xl">
          The line between advice and action is not a setting. It is the architecture.
        </h2>

        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-[var(--color-line)] bg-[var(--color-paper)] p-4">
            <div className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-[var(--color-signal-600)]" />
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--color-signal-700)]">
                What the AI can do
              </p>
            </div>
            <ul className="mt-3 space-y-2.5">
              {CAN.map((c) => (
                <li key={c} className="flex items-start gap-2.5 text-[13px] leading-relaxed text-[var(--color-ink-700)]">
                  <span className="mt-0.5 text-[var(--color-signal-600)]" aria-hidden>+</span>
                  {c}
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-xl border-2 border-red-200 bg-red-50/40 p-4">
            <div className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-red-500" />
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-red-700">
                What it can never do
              </p>
            </div>
            <ul className="mt-3 space-y-2.5">
              {CANNOT.map((c) => (
                <li key={c} className="flex items-start gap-2.5 text-[13px] leading-relaxed text-[var(--color-ink-700)]">
                  <span className="mt-0.5 text-red-500" aria-hidden>✕</span>
                  {c}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="mt-4 grid gap-px overflow-hidden rounded-xl border border-[var(--color-line)] bg-[var(--color-line)] sm:grid-cols-2 lg:grid-cols-5">
          {GATES.map((g) => (
            <div key={g.label} className="bg-[var(--color-surface)] p-4">
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-400)]">{g.label}</p>
              <p className="mt-1.5 font-serif text-xl text-[var(--color-ink-900)]">{g.value}</p>
              <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--color-ink-400)]">{g.note}</p>
            </div>
          ))}
        </div>
      </section>
    </Reveal>
  );
}

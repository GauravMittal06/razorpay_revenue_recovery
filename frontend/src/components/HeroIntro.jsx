import { useRecoveryData } from "../hooks/useRecoveryData";
import { useViewNav } from "../hooks/useViewNav";
import Reveal from "./Reveal";

// Compact narrative header for Overview. Deliberately short — this
// introduces the product story and hands off to the live metrics/Scenario
// Lab immediately below, rather than pushing them down a long scroll like
// the V0 reference does. No fabricated numbers: the only stat shown is the
// real overall_recovery_rate_pct from useRecoveryData, and only once loaded.
const STEPS = [
  { k: "Payment failure", d: "A charge drops. Revenue is now at risk.", tone: "bg-red-500" },
  { k: "Intelligence", d: "The LLM reads intent — it never touches the money.", tone: "bg-[var(--color-signal-600)]" },
  { k: "Decision", d: "The rule engine applies a deterministic policy.", tone: "bg-[var(--color-ink-900)]" },
  { k: "Recovery", d: "The action executes under compliance, and is logged.", tone: "bg-green-600" },
];

export default function HeroIntro() {
  const { metrics } = useRecoveryData();
  const { navigateTo } = useViewNav();

  return (
    <Reveal>
      <section className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] px-6 py-7 sm:px-8 sm:py-9">
        <div className="inline-flex items-center gap-2 rounded-full border border-[var(--color-line)] bg-[var(--color-paper)] px-3 py-1">
          <span className="relative flex size-1.5">
            <span className="absolute inline-flex size-1.5 animate-ping rounded-full bg-[var(--color-signal-600)] opacity-60" />
            <span className="relative inline-flex size-1.5 rounded-full bg-[var(--color-signal-600)]" />
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[var(--color-ink-500)]">
            Revenue Recovery Control Tower
          </span>
          {metrics?.overall_recovery_rate_pct != null && (
            <>
              <span className="h-3 w-px bg-[var(--color-line)]" />
              <span className="font-mono text-[10px] text-[var(--color-signal-700)]">
                {metrics.overall_recovery_rate_pct}% recovered live
              </span>
            </>
          )}
        </div>

        <h1 className="mt-4 max-w-2xl text-balance font-serif text-3xl font-light leading-[1.08] tracking-[-0.01em] text-[var(--color-ink-900)] sm:text-[2.75rem]">
          Recover revenue before it becomes{" "}
          <span className="italic text-[var(--color-signal-600)]">lost</span> revenue.
        </h1>

        <p className="mt-3 max-w-xl text-sm leading-relaxed text-[var(--color-ink-500)] sm:text-[15px]">
          The system reads payment failures and customer intent in real time — while a
          deterministic rule engine stays firmly in control of every financial action.
          Intelligence advises. It never touches the money.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-2.5">
          <button
            onClick={() => navigateTo("live-agent")}
            className="inline-flex items-center gap-1.5 rounded-full bg-[var(--color-ink-900)] px-4 py-2 text-xs font-medium text-white transition-transform hover:-translate-y-0.5"
          >
            Test a recovery scenario
            <span aria-hidden>→</span>
          </button>
          <button
            onClick={() => navigateTo("recovery-queue")}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-line)] bg-[var(--color-paper)] px-4 py-2 text-xs font-medium text-[var(--color-ink-700)] transition-colors hover:border-[var(--color-ink-300)]"
          >
            View recovery queue
          </button>
        </div>

        <ol className="mt-7 grid grid-cols-2 gap-x-4 gap-y-4 border-t border-[var(--color-line-soft)] pt-5 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <li key={s.k} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className={`size-1.5 rounded-full ${s.tone}`} />
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--color-ink-400)]">
                  0{i + 1}
                </span>
              </div>
              <p className="font-serif text-base text-[var(--color-ink-900)]">{s.k}</p>
              <p className="text-[11px] leading-snug text-[var(--color-ink-400)]">{s.d}</p>
            </li>
          ))}
        </ol>
      </section>
    </Reveal>
  );
}

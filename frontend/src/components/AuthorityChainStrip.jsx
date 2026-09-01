// Static restatement of the locked authority boundary (SoT §5), visible in
// the Live Agent view even before a scenario is run. Deliberately separate
// from ReasoningPanel, which shows the same three stages live and driven by
// real lastMutation data — this strip carries no data, just the standing
// architectural claim.
export default function AuthorityChainStrip() {
  return (
    <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-ink-900)] px-5 py-4">
      <p className="font-serif text-lg font-light leading-snug text-white sm:text-xl">
        AI advises. <span className="text-[var(--color-signal-100)] font-normal">The rule engine decides.</span>{" "}
        Compliance controls execution.
      </p>
      <p className="mt-1.5 text-xs leading-relaxed text-white/50">
        Every scenario below runs the real pipeline — nothing here is a client-side simulation.
      </p>
    </div>
  );
}

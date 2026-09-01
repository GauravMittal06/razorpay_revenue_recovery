export default function StagedProgress({ stages, currentIndex }) {
  if (currentIndex < 0) return null;

  return (
    <div className="flex flex-col gap-0 bg-[var(--color-paper)] border border-[var(--color-line)] rounded-lg p-3">
      {stages.map((label, i) => {
        const state = i < currentIndex ? "done" : i === currentIndex ? "active" : "pending";
        return (
          <div key={label} className="flex items-center gap-2.5 py-1">
            <span
              className={`grid size-5 shrink-0 place-items-center rounded-full font-mono text-[10px] transition-colors ${
                state === "done"
                  ? "bg-[var(--color-signal-600)] text-white"
                  : state === "active"
                  ? "bg-[var(--color-signal-100)] text-[var(--color-signal-700)]"
                  : "bg-[var(--color-line-soft)] text-[var(--color-ink-300)]"
              }`}
            >
              {state === "done" ? "✓" : i + 1}
            </span>
            <span
              className={`text-xs transition-colors ${
                state === "pending"
                  ? "text-[var(--color-ink-300)]"
                  : state === "active"
                  ? "text-[var(--color-signal-700)] font-medium"
                  : "text-[var(--color-ink-500)]"
              }`}
            >
              {label}
            </span>
            {state === "active" && (
              <span className="ml-1 flex gap-1" aria-hidden>
                <span className="size-1 animate-bounce rounded-full bg-[var(--color-signal-600)] [animation-delay:-0.2s]" />
                <span className="size-1 animate-bounce rounded-full bg-[var(--color-signal-600)] [animation-delay:-0.1s]" />
                <span className="size-1 animate-bounce rounded-full bg-[var(--color-signal-600)]" />
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
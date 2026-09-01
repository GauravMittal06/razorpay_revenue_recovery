export default function StagedProgress({ stages, currentIndex }) {
  if (currentIndex < 0) return null;

  return (
    <div className="flex flex-col gap-1.5 bg-[var(--color-paper)] border border-[var(--color-line)] rounded-md p-2.5">
      {stages.map((label, i) => {
        const state = i < currentIndex ? "done" : i === currentIndex ? "active" : "pending";
        return (
          <div
            key={label}
            className={`flex items-center gap-2 text-xs transition-colors ${
              state === "pending"
                ? "text-[var(--color-ink-300)]"
                : state === "active"
                ? "text-[var(--color-signal-700)] font-medium"
                : "text-green-700"
            }`}
          >
            <span
              className={`w-3.5 h-3.5 shrink-0 rounded-full flex items-center justify-center text-[9px] leading-none ${
                state === "done"
                  ? "bg-green-600 text-white"
                  : state === "active"
                  ? "border-2 border-[var(--color-signal-600)] animate-pulse"
                  : "border border-[var(--color-line)]"
              }`}
            >
              {state === "done" ? "✓" : ""}
            </span>
            {label}
          </div>
        );
      })}
    </div>
  );
}
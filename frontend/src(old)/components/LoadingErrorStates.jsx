export function LoadingState({ label = "Loading…" }) {
  return (
    <div className="flex items-center gap-2 text-sm text-[var(--color-ink-400)] p-4">
      <span className="inline-block w-3 h-3 rounded-full border-2 border-gray-300 border-t-[var(--color-signal-600)] animate-spin" />
      {label}
    </div>
  );
}

export function ErrorState({ message }) {
  return (
    <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-md p-3">
      {message}
    </div>
  );
}

export function EmptyState({ message }) {
  return (
    <div className="text-sm text-[var(--color-ink-300)] text-center py-6">{message}</div>
  );
}
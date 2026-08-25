export default function ExceptionsPanel({ count, byFlagType }) {
  if (byFlagType == null) return null;

  const isZero = count === 0;

  return (
    <div
      className={`rounded-lg border shadow-sm p-4 ${
        isZero ? "bg-gray-50 border-gray-200" : "bg-amber-50 border-amber-200"
      }`}
    >
      <h3 className="text-sm font-semibold text-gray-800 mb-1">
        Unresolved Exceptions
      </h3>
      <p className="text-xs text-gray-400 mb-3">
        Live count of cases flagged for manual review. Human resolution is not
        currently tracked — this is not a resolved/unresolved queue.
      </p>
      <div className={`text-2xl font-bold mb-3 ${isZero ? "text-gray-400" : "text-amber-700"}`}>
        {count}
      </div>
      <ul className="text-sm text-gray-600 space-y-1">
        {Object.entries(byFlagType).map(([flag, n]) => (
          <li key={flag} className="flex justify-between">
            <span>{flag}</span>
            <span className={`font-medium ${n > 0 ? "text-gray-800" : "text-gray-300"}`}>{n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
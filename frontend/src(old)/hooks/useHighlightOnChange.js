import { useEffect, useRef, useState } from "react";

const DEFAULT_DURATION_MS = 900;

/**
 * Returns true for a short window whenever `value` changes (by reference
 * inequality after JSON-stringify comparison for objects, or plain === for
 * primitives). Used to drive a brief, restrained visual pulse so cause and
 * effect are visible on screen without resorting to motion/scale effects.
 *
 * First render never highlights — only genuine changes after mount do,
 * so the whole UI doesn't flash on initial load.
 */
export function useHighlightOnChange(value, durationMs = DEFAULT_DURATION_MS) {
  const [highlighted, setHighlighted] = useState(false);
  const prevRef = useRef(undefined);
  const isFirstRender = useRef(true);
  const timeoutRef = useRef(null);

  const serialized = typeof value === "object" && value !== null ? JSON.stringify(value) : value;

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      prevRef.current = serialized;
      return;
    }

    if (serialized !== prevRef.current && serialized != null) {
      prevRef.current = serialized;
      setHighlighted(true);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setHighlighted(false), durationMs);
    }

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized, durationMs]);

  return highlighted;
}

/**
 * Variant for lists: returns a Set of "new" item keys (present now but not
 * in the previous render), highlighted briefly. Used for the audit feed and
 * case table so newly-appended entries are visibly distinct without
 * re-flashing the whole list on every poll.
 *
 * `resetSignal` (optional) re-baselines the tracker without flashing —
 * pass something like a stringified filter set so that changing filters
 * (which swaps the whole visible list) never reads as "everything is new".
 */
export function useHighlightNewItems(items, getKey, resetSignal, durationMs = DEFAULT_DURATION_MS) {
  const [newKeys, setNewKeys] = useState(() => new Set());
  const prevKeysRef = useRef(null);
  const prevResetSignalRef = useRef(resetSignal);
  const timeoutRef = useRef(null);

  useEffect(() => {
    const currentKeys = new Set(items.map(getKey));
    const resetSignalChanged = prevResetSignalRef.current !== resetSignal;
    prevResetSignalRef.current = resetSignal;

    if (prevKeysRef.current === null || resetSignalChanged) {
      // First render, or the visible set changed for a reason unrelated to
      // the engine (e.g. a filter change) — establish a fresh baseline
      // instead of treating the whole list as new.
      prevKeysRef.current = currentKeys;
      setNewKeys(new Set());
      return;
    }

    const added = [...currentKeys].filter((k) => !prevKeysRef.current.has(k));
    prevKeysRef.current = currentKeys;

    if (added.length > 0) {
      setNewKeys(new Set(added));
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => setNewKeys(new Set()), durationMs);
    }

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, resetSignal, durationMs]);

  return newKeys;
}
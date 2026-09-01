import { useEffect, useRef, useState } from "react";

const STEP_MS = 420;

/**
 * Honest client-side staged presentation for a single real async request.
 * There is no per-stage backend signal — the backend returns one response
 * for the whole pipeline. So: stages 0..stageCount-2 auto-advance on a
 * timer while `running` is true, as a plain-language sketch of what's
 * likely happening server-side, and simply hold at the last "in progress"
 * stage if the real request is still pending. The FINAL stage
 * (stageCount-1) only ever renders once `succeeded` actually becomes true —
 * i.e. after the real request resolved. Nothing is ever claimed complete
 * ahead of the real result.
 *
 * Returns -1 when idle, 0..stageCount-1 while running/just-succeeded.
 */
export function useStagedProgress(stageCount, running, succeeded) {
  const [index, setIndex] = useState(-1);
  const intervalRef = useRef(null);

  useEffect(() => {
    if (running) {
      setIndex(0);
      intervalRef.current = setInterval(() => {
        setIndex((i) => (i < stageCount - 2 ? i + 1 : i));
      }, STEP_MS);
    } else {
      clearInterval(intervalRef.current);
    }
    return () => clearInterval(intervalRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, stageCount]);

  useEffect(() => {
    if (succeeded) {
      clearInterval(intervalRef.current);
      setIndex(stageCount - 1);
    }
  }, [succeeded, stageCount]);

  return index;
}
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  fetchMetrics,
  fetchCases,
  fetchAuditFeed,
  triggerEvent,
  submitReply,
  simulateRecovery,
} from "../api/client";

// Server/live data only. UI-only state (which case is selected, panel focus,
// etc.) intentionally lives outside this store — see useActiveCase.js.

const POLL_INTERVAL_MS = 6000;
const AUDIT_FEED_LIMIT = 20;

const RecoveryDataContext = createContext(null);

const EMPTY_FILTERS = { event_type: "", recovery_status: "", outcome: "" };

export function RecoveryDataProvider({ children }) {
  const [metrics, setMetrics] = useState(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [metricsError, setMetricsError] = useState(null);

  const [cases, setCases] = useState([]);
  const [casesLoading, setCasesLoading] = useState(true);
  const [casesError, setCasesError] = useState(null);
  const [caseFilters, setCaseFilters] = useState(EMPTY_FILTERS);

  const [auditFeed, setAuditFeed] = useState([]);
  const [auditFeedLoading, setAuditFeedLoading] = useState(true);

  // Tracks the most recent mutation result so consumers (ReasoningPanel) can
  // drive a real reveal sequence only when something genuinely just happened.
  const [lastMutation, setLastMutation] = useState(null);

  const inFlightRef = useRef(false);
  const filtersRef = useRef(caseFilters);
  filtersRef.current = caseFilters;

  const loadMetrics = useCallback(async () => {
    try {
      const data = await fetchMetrics();
      setMetrics(data);
      setMetricsError(null);
    } catch {
      setMetricsError("Could not reach /api/metrics. Is the backend running?");
    } finally {
      setMetricsLoading(false);
    }
  }, []);

  const loadCases = useCallback(async () => {
    try {
      const data = await fetchCases(filtersRef.current);
      setCases(data);
      setCasesError(null);
    } catch {
      setCasesError("Could not reach /api/cases. Is the backend running?");
    } finally {
      setCasesLoading(false);
    }
  }, []);

  const loadAuditFeed = useCallback(async () => {
    try {
      const data = await fetchAuditFeed(AUDIT_FEED_LIMIT);
      setAuditFeed(data);
    } catch {
      // Audit feed failures are non-fatal to the rest of the console; the
      // panel itself shows its own empty/stale state.
    } finally {
      setAuditFeedLoading(false);
    }
  }, []);

  const refetchAll = useCallback(async () => {
    await Promise.all([loadMetrics(), loadCases(), loadAuditFeed()]);
  }, [loadMetrics, loadCases, loadAuditFeed]);

  // Initial load
  useEffect(() => {
    refetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refetch cases whenever filters change (independent of the poll cycle).
  useEffect(() => {
    setCasesLoading(true);
    loadCases();
  }, [caseFilters, loadCases]);

  // Lightweight polling so both surfaces stay genuinely live, not just
  // live-after-action. Paused while a mutation is in flight to avoid
  // fetching stale data mid-write or racing the mutation's own refetch.
  useEffect(() => {
    const id = setInterval(() => {
      if (inFlightRef.current) return;
      refetchAll();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refetchAll]);

  async function runTrigger(payload) {
    inFlightRef.current = true;
    try {
      const result = await triggerEvent(payload);
      setLastMutation({ kind: "trigger", result, at: Date.now() });
      await refetchAll();
      return result;
    } finally {
      inFlightRef.current = false;
    }
  }

  async function runReply(paymentId, message) {
    inFlightRef.current = true;
    try {
      const result = await submitReply(paymentId, message);
      setLastMutation({ kind: "reply", result, at: Date.now() });
      await refetchAll();
      return result;
    } finally {
      inFlightRef.current = false;
    }
  }

  async function runSimulate(paymentId) {
    inFlightRef.current = true;
    try {
      const result = await simulateRecovery(paymentId);
      setLastMutation({ kind: "simulate", result, at: Date.now() });
      await refetchAll();
      return result;
    } finally {
      inFlightRef.current = false;
    }
  }

  const value = {
    metrics,
    metricsLoading,
    metricsError,
    reloadMetrics: loadMetrics,

    cases,
    casesLoading,
    casesError,
    caseFilters,
    setCaseFilters,

    auditFeed,
    auditFeedLoading,

    lastMutation,

    refetchAll,
    runTrigger,
    runReply,
    runSimulate,
  };

  return (
    <RecoveryDataContext.Provider value={value}>
      {children}
    </RecoveryDataContext.Provider>
  );
}

export function useRecoveryData() {
  const ctx = useContext(RecoveryDataContext);
  if (!ctx) {
    throw new Error("useRecoveryData must be used within a RecoveryDataProvider");
  }
  return ctx;
}
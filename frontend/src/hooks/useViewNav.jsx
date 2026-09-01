import { createContext, useContext, useState } from "react";
import { useRecoveryData } from "./useRecoveryData";

// UI/navigation state — which of the 6 real views is active. Kept separate
// from useRecoveryData (server data) but declared inside that provider
// since navigating to Exceptions applies a real, existing filter.
const ViewNavContext = createContext(null);

export function ViewNavProvider({ children }) {
  const [activeView, setActiveView] = useState("overview");
  const { setCaseFilters } = useRecoveryData();

  function navigateTo(viewId) {
    setActiveView(viewId);
    if (viewId === "exceptions") {
      // Reuses CaseTable's own existing outcome filter — not a new
      // mechanism, just a helpful default when arriving at this view.
      setCaseFilters((f) => ({ ...f, outcome: "flagged_manual_review" }));
    }
  }

  return (
    <ViewNavContext.Provider value={{ activeView, navigateTo }}>
      {children}
    </ViewNavContext.Provider>
  );
}

export function useViewNav() {
  const ctx = useContext(ViewNavContext);
  if (!ctx) {
    throw new Error("useViewNav must be used within a ViewNavProvider");
  }
  return ctx;
}
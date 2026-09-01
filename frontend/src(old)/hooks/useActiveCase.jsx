import { createContext, useContext, useState } from "react";

// Deliberately separate from useRecoveryData: this is UI/navigation state
// (which case the operator is currently looking at), not server data. It's
// promoted out of a single component because both the Dashboard case table
// and the Live Console genuinely need to read/set the same value — e.g.
// triggering an event in the console should make that case the one shown
// in the dashboard's case detail, and selecting a case on the dashboard
// should make the console's reply box default to it.

const ActiveCaseContext = createContext(null);

export function ActiveCaseProvider({ children }) {
  const [activeCaseId, setActiveCaseId] = useState(null);
  return (
    <ActiveCaseContext.Provider value={{ activeCaseId, setActiveCaseId }}>
      {children}
    </ActiveCaseContext.Provider>
  );
}

export function useActiveCase() {
  const ctx = useContext(ActiveCaseContext);
  if (!ctx) {
    throw new Error("useActiveCase must be used within an ActiveCaseProvider");
  }
  return ctx;
}
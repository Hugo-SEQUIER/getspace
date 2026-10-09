
import { createContext, useContext, useState, type ReactNode } from "react";
import type { ConsentResult } from "../../../lib/device-flow";

const FlowContext = createContext<{
  result: ConsentResult | null;
  complete: (result: ConsentResult) => void;
} | null>(null);

export function ConnectFlowProvider({ children }: { children: ReactNode }) {
  // Keep the decision in memory only, out of URLs and browser storage.
  const [result, complete] = useState<ConsentResult | null>(null);
  return (
    <FlowContext.Provider value={{ result, complete }}>
      {children}
    </FlowContext.Provider>
  );
}

export function useConnectFlow() {
  const flow = useContext(FlowContext);
  if (!flow) throw new Error("Connection pages require ConnectFlowProvider");
  return flow;
}

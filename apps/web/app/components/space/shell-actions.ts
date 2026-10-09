import { createContext, useContext } from "react";

type ShellActions = {
  onSearch: () => void;
  searchShortcut: string;
};

export const ShellActionsContext = createContext<ShellActions | null>(null);

export function useShellActions(): ShellActions {
  const actions = useContext(ShellActionsContext);
  if (!actions) throw new Error("Shell actions require SpaceApp");
  return actions;
}

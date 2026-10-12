import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface SettingsUiValue {
  isOpen: boolean;
  open: () => void;
  close: () => void;
  closedCount: number;
}

const _defaultValue: SettingsUiValue = {
  isOpen: false,
  open: () => undefined,
  close: () => undefined,
  closedCount: 0,
};

export const SettingsUiContext = createContext<SettingsUiValue>(_defaultValue);

export function useSettingsUi(): SettingsUiValue {
  return useContext(SettingsUiContext);
}

export function SettingsUiProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [isOpen, setIsOpen] = useState(false);
  const [closedCount, setClosedCount] = useState(0);

  const open = useCallback(() => {
    setIsOpen(true);
  }, []);

  // closedCount increments on every close, not just a plain isOpen flip, so
  // Home's useConnectStates can refetch secrets after a Settings visit — the
  // Credentials section's writes emit no SSE change event of their own.
  const close = useCallback(() => {
    setIsOpen(false);
    setClosedCount((count) => count + 1);
  }, []);

  const value = useMemo(
    () => ({ isOpen, open, close, closedCount }),
    [isOpen, open, close, closedCount],
  );

  return <SettingsUiContext.Provider value={value}>{children}</SettingsUiContext.Provider>;
}

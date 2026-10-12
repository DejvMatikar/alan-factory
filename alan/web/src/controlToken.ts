export const CONTROL_TOKEN_HEADER = "X-Alan-Control-Token";
export const CONTROL_TOKEN_STORAGE_KEY = "alan.controlToken";

type ControlTokenListener = () => void;

const listeners = new Set<ControlTokenListener>();

// Same-tab consumers (useControlTokenPresent's useSyncExternalStore) can't
// see a plain localStorage write — the browser's own "storage" event only
// fires in *other* tabs. This module is the single writer surface
// (SettingsModal.tsx is its only caller), so notifying here covers every
// same-tab write path without a second event mechanism.
export function subscribeControlToken(listener: ControlTokenListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notifyControlTokenListeners(): void {
  for (const listener of listeners) listener();
}

export function readControlToken(): string {
  return localStorage.getItem(CONTROL_TOKEN_STORAGE_KEY) ?? "";
}

export function writeControlToken(value: string): void {
  const trimmed = value.trim();
  if (trimmed) {
    localStorage.setItem(CONTROL_TOKEN_STORAGE_KEY, trimmed);
  } else {
    localStorage.removeItem(CONTROL_TOKEN_STORAGE_KEY);
  }
  notifyControlTokenListeners();
}

export function clearControlToken(): void {
  localStorage.removeItem(CONTROL_TOKEN_STORAGE_KEY);
  notifyControlTokenListeners();
}

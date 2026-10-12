export const PROJECT_FEATURES_STORAGE_KEY = "alan.projectFeatures";

type ProjectFeaturesListener = () => void;

const listeners = new Set<ProjectFeaturesListener>();

// Same-tab consumers (useProjectFeatures's useSyncExternalStore) can't see a
// plain localStorage write — the browser's own "storage" event only fires in
// *other* tabs. This module is the single writer surface (components/ProjectFeaturesToggle.tsx
// is its only caller), so notifying here covers every same-tab write path
// without a second event mechanism.
export function subscribeProjectFeatures(listener: ProjectFeaturesListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notifyProjectFeaturesListeners(): void {
  for (const listener of listeners) listener();
}

export function readProjectFeaturesOn(): boolean {
  return localStorage.getItem(PROJECT_FEATURES_STORAGE_KEY) === "on";
}

export function writeProjectFeaturesOn(value: boolean): void {
  if (value) {
    localStorage.setItem(PROJECT_FEATURES_STORAGE_KEY, "on");
  } else {
    localStorage.removeItem(PROJECT_FEATURES_STORAGE_KEY);
  }
  notifyProjectFeaturesListeners();
}

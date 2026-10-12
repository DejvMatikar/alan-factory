import { useSyncExternalStore } from "react";

import { readProjectFeaturesOn, subscribeProjectFeatures } from "../projectFeatures";

export function useProjectFeatures(): boolean {
  return useSyncExternalStore(subscribeProjectFeatures, readProjectFeaturesOn);
}

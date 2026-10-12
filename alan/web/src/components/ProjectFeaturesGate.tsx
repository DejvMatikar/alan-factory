import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useProjectFeatures } from "../hooks/useProjectFeatures";

export default function ProjectFeaturesGate({ children }: Readonly<{ children: ReactNode }>) {
  const projectFeaturesOn = useProjectFeatures();
  if (!projectFeaturesOn) return <Navigate to="/" replace />;
  return <>{children}</>;
}

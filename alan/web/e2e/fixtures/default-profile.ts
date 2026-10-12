import fs from "node:fs";
import path from "node:path";

import { enginesRoot, repoRoot } from "./server-env";

export const TEST_DEFAULT_PROFILE = path.join(
  repoRoot,
  "tests",
  "fixtures",
  "default_profile_test.yaml",
);

export function installedDefaultProfile(root: string): string {
  return path.join(enginesRoot(root), "default_profile.yaml");
}

export function installTestDefaultProfile(root: string): void {
  const target = installedDefaultProfile(root);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(TEST_DEFAULT_PROFILE, target);
}

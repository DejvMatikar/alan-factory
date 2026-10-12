import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { resolveCached, resolveCachedPort } from "./lib/derived-run-config";

const here = path.dirname(fileURLToPath(import.meta.url));

export function resolveCapturePort(): number {
  return resolveCachedPort("ALAN_CAPTURE_PORT", 4700);
}

export function resolveCaptureRoot(): string {
  return resolveCached("ALAN_CAPTURE_ROOT", () =>
    path.join(os.tmpdir(), "alan-screen-capture", `pid-${process.pid}`),
  );
}

// The built UI artifact (alan/web/dist, what the live production service
// serves) and the screenshot output directory (alan/web/screen-captures) are
// the two shared-collision points a concurrent capture run must never touch
// — see docs/architecture/screen_capture.md's "Shared-checkout concurrency"
// section. Both derive functions are pure (pid in, path out) so
// capture-config.identity.test.ts can assert per-pid isolation directly.
export function deriveBuildDir(pid: number): string {
  return path.join(os.tmpdir(), "alan-screen-capture-build", `pid-${pid}`);
}

export function deriveScreensDir(pid: number): string {
  return path.join(here, "..", "screen-captures", `pid-${pid}`);
}

export function resolveBuildDir(): string {
  return resolveCached("ALAN_WEB_DIST_DIR", () => deriveBuildDir(process.pid));
}

export function resolveScreensDir(): string {
  return resolveCached("ALAN_CAPTURE_SCREENS_DIR", () => deriveScreensDir(process.pid));
}

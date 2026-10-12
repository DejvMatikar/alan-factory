import os from "node:os";
import path from "node:path";

import { afterEach, expect, it } from "vitest";

import {
  deriveBuildDir,
  deriveScreensDir,
  resolveBuildDir,
  resolveCapturePort,
  resolveCaptureRoot,
  resolveScreensDir,
} from "./capture-config";
import { candidatePorts } from "./port-avoidance";

const DIST_DIR = path.resolve(process.cwd(), "dist");

const ENV_KEYS = [
  "ALAN_WEB_DIST_DIR",
  "ALAN_CAPTURE_SCREENS_DIR",
  "ALAN_CAPTURE_ROOT",
  "ALAN_CAPTURE_PORT",
] as const;

const originalEnv = Object.fromEntries(
  ENV_KEYS.map((key) => [key, process.env[key]]),
) as Record<(typeof ENV_KEYS)[number], string | undefined>;

afterEach(() => {
  for (const key of ENV_KEYS) {
    const original = originalEnv[key];
    if (original === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = original;
    }
  }
});

it("derives a distinct, tmp-rooted build dir per pid, never the live dist artifact", () => {
  const pidA = 111;
  const pidB = 222;
  expect(deriveBuildDir(pidA)).not.toBe(deriveBuildDir(pidB));
  expect(deriveBuildDir(pidA)).toBe(
    path.join(os.tmpdir(), "alan-screen-capture-build", `pid-${pidA}`),
  );
  expect(deriveBuildDir(pidA)).not.toBe(DIST_DIR);
});

it("derives a distinct, screen-captures-rooted screenshot dir per pid, never the live dist artifact", () => {
  const pidA = 111;
  const pidB = 222;
  expect(deriveScreensDir(pidA)).not.toBe(deriveScreensDir(pidB));
  expect(deriveScreensDir(pidA)).toBe(
    path.resolve(process.cwd(), "screen-captures", `pid-${pidA}`),
  );
  expect(deriveScreensDir(pidA)).not.toBe(DIST_DIR);
});

it("resolveBuildDir returns the pre-set env value unchanged on every call", () => {
  const preset = path.join(os.tmpdir(), "capture-config-identity-test-build");
  process.env.ALAN_WEB_DIST_DIR = preset;
  expect(resolveBuildDir()).toBe(preset);
  expect(resolveBuildDir()).toBe(preset);
});

it("resolveBuildDir derives and caches a fresh value when unset", () => {
  delete process.env.ALAN_WEB_DIST_DIR;
  const resolved = resolveBuildDir();
  expect(resolved).toBe(deriveBuildDir(process.pid));
  expect(process.env.ALAN_WEB_DIST_DIR).toBe(resolved);
});

it("resolveBuildDir derives a fresh value when set to whitespace only", () => {
  process.env.ALAN_WEB_DIST_DIR = "   ";
  const resolved = resolveBuildDir();
  expect(resolved).toBe(deriveBuildDir(process.pid));
  expect(process.env.ALAN_WEB_DIST_DIR).toBe(resolved);
});

it("resolveScreensDir returns the pre-set env value unchanged on every call", () => {
  const preset = path.join(os.tmpdir(), "capture-config-identity-test-screens");
  process.env.ALAN_CAPTURE_SCREENS_DIR = preset;
  expect(resolveScreensDir()).toBe(preset);
  expect(resolveScreensDir()).toBe(preset);
});

it("resolveScreensDir derives and caches a fresh value when unset", () => {
  delete process.env.ALAN_CAPTURE_SCREENS_DIR;
  const resolved = resolveScreensDir();
  expect(resolved).toBe(deriveScreensDir(process.pid));
  expect(process.env.ALAN_CAPTURE_SCREENS_DIR).toBe(resolved);
});

it("resolveCaptureRoot returns the pre-set env value unchanged on every call", () => {
  const preset = path.join(os.tmpdir(), "capture-config-identity-test-root");
  process.env.ALAN_CAPTURE_ROOT = preset;
  expect(resolveCaptureRoot()).toBe(preset);
  expect(resolveCaptureRoot()).toBe(preset);
});

it("resolveCaptureRoot derives and caches a fresh value when unset", () => {
  delete process.env.ALAN_CAPTURE_ROOT;
  const resolved = resolveCaptureRoot();
  expect(resolved).toBe(path.join(os.tmpdir(), "alan-screen-capture", `pid-${process.pid}`));
  expect(process.env.ALAN_CAPTURE_ROOT).toBe(resolved);
});

it("resolveCapturePort returns the pre-set env value unchanged when it is a valid positive integer", () => {
  process.env.ALAN_CAPTURE_PORT = "4321";
  expect(resolveCapturePort()).toBe(4321);
  expect(resolveCapturePort()).toBe(4321);
});

it("resolveCapturePort derives and caches a fresh port when unset or invalid", () => {
  process.env.ALAN_CAPTURE_PORT = "not-a-port";
  const resolved = resolveCapturePort();
  expect(resolved).toBe(candidatePorts(4700, process.pid)[0]);
  expect(process.env.ALAN_CAPTURE_PORT).toBe(String(resolved));
});

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "..");
const distDir = path.join(webRoot, "dist");
const screenCapturesDir = path.join(webRoot, "screen-captures");

const EXPECTED_SCREEN_FILES = [
  "home.desktop.png",
  "home.mobile.png",
  "build.desktop.png",
  "build.mobile.png",
  "explore-architecture.desktop.png",
  "explore-architecture.mobile.png",
  "health.desktop.png",
  "health.mobile.png",
  "projects.desktop.png",
  "projects.mobile.png",
  "project-detail.desktop.png",
  "project-detail.mobile.png",
];

function hashFile(filePath) {
  return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function snapshotDir(dir) {
  const snapshot = {};
  if (!fs.existsSync(dir)) {
    return snapshot;
  }
  const walk = (current) => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile()) {
        snapshot[path.relative(dir, full)] = hashFile(full);
      }
    }
  };
  walk(dir);
  return snapshot;
}

function snapshotsEqual(a, b) {
  const aKeys = Object.keys(a).sort();
  const bKeys = Object.keys(b).sort();
  if (aKeys.length !== bKeys.length) {
    return false;
  }
  return aKeys.every((key, i) => key === bKeys[i] && a[key] === b[key]);
}

function runCapture() {
  return new Promise((resolve, reject) => {
    const child = spawn("npm", ["run", "capture:screens"], {
      cwd: webRoot,
      stdio: "inherit",
    });
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`capture:screens exited with code ${code}`));
      }
    });
    child.on("error", reject);
  });
}

function listRunDirs() {
  if (!fs.existsSync(screenCapturesDir)) {
    return [];
  }
  return fs
    .readdirSync(screenCapturesDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && entry.name.startsWith("pid-"))
    .map((entry) => path.join(screenCapturesDir, entry.name));
}

async function main() {
  const before = snapshotDir(distDir);
  const preExistingRunDirs = new Set(listRunDirs());

  // Both runs are started back-to-back, synchronously, before either is
  // awaited — this is what "started at the same time" means here. No env is
  // set for either child: pid-derivation must keep them apart unassisted.
  await Promise.all([runCapture(), runCapture()]);

  const after = snapshotDir(distDir);
  if (!snapshotsEqual(before, after)) {
    throw new Error("alan/web/dist changed during concurrent capture runs");
  }

  const runDirs = listRunDirs().filter((dir) => !preExistingRunDirs.has(dir));
  if (runDirs.length !== 2) {
    throw new Error(
      `expected 2 fresh per-run screenshot dirs, found ${runDirs.length}: ${runDirs.join(", ")}`,
    );
  }

  for (const dir of runDirs) {
    const files = fs.readdirSync(dir);
    const missing = EXPECTED_SCREEN_FILES.filter((name) => !files.includes(name));
    if (missing.length > 0) {
      throw new Error(`${dir} is missing screenshots: ${missing.join(", ")}`);
    }
  }

  console.log(`OK: alan/web/dist unchanged; ${runDirs.length} independent capture runs completed`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});

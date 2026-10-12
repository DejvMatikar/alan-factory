// Generates one NNN-<name>.spec.ts shim per top-level e2e spec, each just
// `import "<relative path to the original>";` — Playwright registers tests
// at import time, and the original spec's OWN relative imports (e.g.
// "./fixtures/test-base") resolve from the original's own file location
// regardless of who imported it, so the shim needs no other content.
//
// NNN is assigned ascending as the specs' alphabetical order descends: shim
// 001 imports the alphabetically LAST original spec, the highest NNN imports
// the alphabetically FIRST one. Playwright enumerates and runs testDir's spec
// files in name order, so pointing testDir at this directory (see
// playwright.reversed.config.ts) runs specs in the reverse of their normal
// alphabetical order — CLI argument order cannot achieve this, since
// Playwright sorts discovered files itself regardless of how they're passed.
//
// Lives OUTSIDE e2e/ specifically so playwright.config.ts's testDir: "./e2e"
// never discovers these as extra tests in the normal suite — no change to
// that file's fullyParallel/retries/workers (T2-11) or testIgnore needed.
// Gitignored (regenerated fresh each run) and eslint-ignored (see
// eslint.config.js); already outside jscpd's `jscpd src` scan root.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "..");
const e2eDir = path.join(webRoot, "e2e");
export const shimDir = path.join(webRoot, ".reversed-shims");
const SKIP_DIRS = new Set(["mobile-audit", "capture"]);

function listSpecs() {
  const specs = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        walk(path.join(dir, entry.name));
      } else if (entry.isFile() && entry.name.endsWith(".spec.ts")) {
        specs.push(path.join(dir, entry.name));
      }
    }
  }
  walk(e2eDir);
  return specs.sort();
}

export function generateShims() {
  fs.rmSync(shimDir, { recursive: true, force: true });
  fs.mkdirSync(shimDir, { recursive: true });

  const specs = listSpecs();
  const reversed = [...specs].reverse();

  for (const [i, specPath] of reversed.entries()) {
    const nnn = String(i + 1).padStart(3, "0");
    const shimName = `${nnn}-${path.basename(specPath)}`;
    const importPath = path.relative(shimDir, specPath).split(path.sep).join("/");
    const importSpecifier = importPath.startsWith(".") ? importPath : `./${importPath}`;
    fs.writeFileSync(path.join(shimDir, shimName), `import ${JSON.stringify(importSpecifier)};\n`);
  }

  return reversed.length;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const count = generateShims();
  console.log(`Generated ${String(count)} reversed-order shims in ${shimDir}`);
}

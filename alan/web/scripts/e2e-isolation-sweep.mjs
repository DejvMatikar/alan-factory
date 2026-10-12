// Runs every e2e spec ALONE, one `playwright test <file> --workers=1` per
// spec against a freshly booted backend, to establish per-spec
// self-sufficiency mechanically rather than by hand: a spec that passes
// alone provably depends on no sibling's server state. This proves ONLY
// that — it does not prove a spec tolerates pollution FROM a sibling, which
// is a different, unclaimed property.
//
// Deliberately kept out of per-PR CI (see package.json's e2e:isolation-sweep
// script, not wired into any CI job): one server boot per spec would dwarf
// the existing ~2-minute e2e job. Run by hand and commit the JSON output as
// a one-time dated audit artifact — see docs/audits/frontend-test-isolation-2026-08/.
//
// Concurrency here means several `playwright test` CHILD PROCESSES run at
// once, each internally still single-worker (--workers=1). Every such
// process sees parallelIndex 0, so port/root are given a per-slot override
// here (disjoint from the normal e2e/capture/mobile-audit pid-derived
// ranges — see server-env.ts) and the shared-checkout git branch collision
// is closed at the fixture level by server-env.ts's ghBranchSuffix, which
// folds in process.pid.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, "..");
const e2eDir = path.join(webRoot, "e2e");
const outputPath = path.resolve(
  webRoot,
  "..",
  "..",
  "docs",
  "audits",
  "frontend-test-isolation-2026-08",
  "e2e-isolation-sweep.json",
);

function readConcurrencyArg() {
  const flag = process.argv.find((a) => a.startsWith("--concurrency="));
  if (!flag) return null;
  const value = Number(flag.slice("--concurrency=".length));
  return Number.isInteger(value) && value > 0 ? value : null;
}

const CONCURRENCY = readConcurrencyArg() ?? 4;
// Disjoint from e2e (4300-4303), screen capture (4700-5699) and mobile-audit
// (5700-6699) — see e2e/fixtures/server-env.ts and src/derived-run-config.ts.
const SWEEP_PORT_BASE = 6700;
const SWEEP_ROOT_BASE = path.join(os.tmpdir(), "alan-playwright-isolation-sweep");
const SKIP_DIRS = new Set(["mobile-audit", "capture"]);

function listSpecs() {
  const specs = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (SKIP_DIRS.has(entry.name)) continue;
        walk(path.join(dir, entry.name));
      } else if (entry.isFile() && entry.name.endsWith(".spec.ts")) {
        specs.push(path.relative(webRoot, path.join(dir, entry.name)));
      }
    }
  }
  walk(e2eDir);
  return specs.sort();
}

function collectFailures(suite, out) {
  for (const child of suite.suites ?? []) collectFailures(child, out);
  for (const spec of suite.specs ?? []) {
    for (const test of spec.tests ?? []) {
      for (const result of test.results ?? []) {
        if (result.status !== "passed" && result.error?.message) {
          out.push(`${spec.title}: ${String(result.error.message).split("\n")[0]}`);
        }
      }
    }
  }
}

function extractRunSummary(stdout) {
  try {
    const parsed = JSON.parse(stdout);
    const failures = [];
    for (const suite of parsed.suites ?? []) collectFailures(suite, failures);
    return { stats: parsed.stats ?? null, failures };
  } catch {
    return { stats: null, failures: [] };
  }
}

function tail(text, lines = 30) {
  return text.split("\n").slice(-lines).join("\n");
}

function runSpecOnce(spec, slot) {
  return new Promise((resolve) => {
    const port = SWEEP_PORT_BASE + slot;
    const root = path.join(SWEEP_ROOT_BASE, `slot-${String(slot)}`);
    const child = spawn(
      "npx",
      ["playwright", "test", spec, "--workers=1", "--retries=0", "--reporter=json"],
      {
        cwd: webRoot,
        env: {
          ...process.env,
          E2E_PORT: String(port),
          ALAN_E2E_ROOT: root,
        },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => {
      stdout += String(d);
    });
    child.stderr.on("data", (d) => {
      stderr += String(d);
    });
    child.on("exit", (code) => {
      const summary = extractRunSummary(stdout);
      resolve({ code: code ?? 1, stats: summary.stats, failures: summary.failures, stderrTail: tail(stderr) });
    });
  });
}

async function runSpec(spec, slot) {
  const first = await runSpecOnce(spec, slot);
  if (first.code === 0) return { spec, status: "pass", stats: first.stats };
  const second = await runSpecOnce(spec, slot);
  if (second.code === 0) {
    return {
      spec,
      status: "pass_on_rerun",
      stats: second.stats,
      firstAttempt: { stats: first.stats, failures: first.failures, stderrTail: first.stderrTail },
    };
  }
  return {
    spec,
    status: "fail",
    firstAttempt: { stats: first.stats, failures: first.failures, stderrTail: first.stderrTail },
    secondAttempt: { stats: second.stats, failures: second.failures, stderrTail: second.stderrTail },
  };
}

function summarize(results) {
  return results.reduce(
    (acc, r) => {
      acc[r.status] = (acc[r.status] ?? 0) + 1;
      return acc;
    },
    { pass: 0, pass_on_rerun: 0, fail: 0 },
  );
}

function writeReport(targets, results, timestamp) {
  const sorted = [...results].sort((a, b) => a.spec.localeCompare(b.spec));
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(
    outputPath,
    `${JSON.stringify(
      {
        generatedAt: timestamp,
        specCount: targets.length,
        blindSpot:
          "proves a spec does not depend on sibling state (passes alone); does NOT prove a spec tolerates pollution from a sibling",
        results: sorted,
        summary: summarize(sorted),
      },
      null,
      2,
    )}\n`,
  );
}

async function main() {
  const timestamp = new Date().toISOString();
  const args = process.argv.slice(2);
  const fresh = args.includes("--fresh");
  const only = args.filter((a) => !a.startsWith("--"));

  const specs = listSpecs();
  const targets = only.length > 0 ? specs.filter((s) => only.some((o) => s.includes(o))) : specs;

  let done = new Map();
  if (!fresh && fs.existsSync(outputPath)) {
    try {
      const existing = JSON.parse(fs.readFileSync(outputPath, "utf8"));
      done = new Map((existing.results ?? []).map((r) => [r.spec, r]));
    } catch {
      done = new Map();
    }
  }

  fs.mkdirSync(SWEEP_ROOT_BASE, { recursive: true });

  const queue = targets.filter((s) => !done.has(s));
  const results = [...done.values()];
  let nextIndex = 0;

  async function worker(slot) {
    for (;;) {
      const i = nextIndex;
      nextIndex += 1;
      if (i >= queue.length) return;
      const spec = queue[i];
      console.log(`[slot ${String(slot)}] ${spec} (${String(i + 1)}/${String(queue.length)})`);
      const result = await runSpec(spec, slot);
      results.push(result);
      console.log(`[slot ${String(slot)}] ${spec}: ${result.status}`);
      writeReport(targets, results, timestamp);
    }
  }

  await Promise.all(
    Array.from({ length: Math.max(1, Math.min(CONCURRENCY, queue.length)) }, (_unused, i) =>
      worker(i),
    ),
  );

  writeReport(targets, results, timestamp);

  const summary = summarize(results);
  console.log(`Sweep complete: ${JSON.stringify(summary)}`);
  if (summary.fail > 0) process.exitCode = 1;
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});

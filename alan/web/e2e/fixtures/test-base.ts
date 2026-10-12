import { expect, test as base } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { startServer, type E2eServer } from "./e2e-server";
import { registerGuardEngine } from "./seed";

const NO_CLAUDE_TAG = "@no-claude";

const ONE_SHOT_TRIGGERS = [
  "stub-hang",
  "usage-limit-trigger",
  "server-error-trigger",
  "cannot-verify-trigger",
  "rendezvous.log",
  "gh-pr-state.json",
  "gh-checks-state",
  "gh-copilot-threads",
  "gh-copilot-comments",
];

interface WorkerFixtures {
  e2eServer: E2eServer;
  e2eRoot: string;
}

export interface SeedEngineSpec {
  owner: string;
  repo?: string;
}

const LOG_QUIESCE_POLL_MS = 100;
const LOG_QUIESCE_STABLE_MS = 300;
const LOG_QUIESCE_MAX_WAIT_MS = 2_000;

function logSize(logPath: string): number {
  try {
    return fs.statSync(logPath).size;
  } catch {
    return 0;
  }
}

async function waitForLogQuiescence(logPath: string): Promise<void> {
  const deadline = Date.now() + LOG_QUIESCE_MAX_WAIT_MS;
  let lastSize = logSize(logPath);
  let stableSince = Date.now();
  for (;;) {
    if (Date.now() - stableSince >= LOG_QUIESCE_STABLE_MS) return;
    if (Date.now() >= deadline) return;
    await new Promise((resolve) => setTimeout(resolve, LOG_QUIESCE_POLL_MS));
    const size = logSize(logPath);
    if (size !== lastSize) {
      lastSize = size;
      stableSince = Date.now();
    }
  }
}

interface TestFixtures {
  stubGuard: void;
  triggerHygiene: void;
  serverLogOnFailure: void;
  machineLoadOnFailure: void;
  seedEngines: SeedEngineSpec[];
  seededEngines: void;
}

export const test = base.extend<TestFixtures, WorkerFixtures>({
  e2eServer: [
    // eslint-disable-next-line no-empty-pattern -- worker fixture has no deps
    async ({}, use, workerInfo) => {
      const server = await startServer(workerInfo.parallelIndex);
      await use(server);
      await server.stop();
    },
    { scope: "worker", timeout: 90_000 },
  ],

  e2eRoot: [
    async ({ e2eServer }, use) => {
      await use(e2eServer.root);
    },
    { scope: "worker" },
  ],

  baseURL: async ({ e2eServer }, provide) => {
    await provide(e2eServer.baseURL);
  },

  seedEngines: [[], { option: true }],

  seededEngines: [
    async ({ request, e2eRoot, seedEngines }, use) => {
      for (const { owner, repo } of seedEngines) {
        await registerGuardEngine(request, e2eRoot, owner, repo);
      }
      await use();
    },
    { auto: true },
  ],

  triggerHygiene: [
    async ({ e2eServer }, use) => {
      for (const name of ONE_SHOT_TRIGGERS) {
        fs.rmSync(path.join(e2eServer.root, name), { force: true });
      }
      await use();
    },
    { auto: true },
  ],

  stubGuard: [
    async ({ e2eServer }, use, testInfo) => {
      const marker = path.join(e2eServer.root, "stub-marker.log");
      const markerSize = (): number => {
        try {
          return fs.statSync(marker).size;
        } catch {
          return 0;
        }
      };
      const baseline = markerSize();
      await use();
      if (testInfo.tags.includes(NO_CLAUDE_TAG)) return;
      if (testInfo.status === "skipped") return;
      if (testInfo.status !== testInfo.expectedStatus) return;
      const deadline = Date.now() + 2_000;
      while (markerSize() === baseline) {
        if (Date.now() > deadline) {
          throw new Error(
            `stub-claude was never invoked during "${testInfo.title}". ` +
              `PATH bypass or missing ALAN_E2E_STUB_MARKER? Marker: ${marker}. ` +
              `Tag this test with "${NO_CLAUDE_TAG}" if it legitimately skips claude.`,
          );
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
    },
    { auto: true },
  ],

  serverLogOnFailure: [
    async ({ e2eServer }, use, testInfo) => {
      const offset = fs.existsSync(e2eServer.logPath)
        ? fs.statSync(e2eServer.logPath).size
        : 0;
      await use();
      if (testInfo.status === testInfo.expectedStatus) return;
      await waitForLogQuiescence(e2eServer.logPath);
      let body: Buffer;
      try {
        body = fs.readFileSync(e2eServer.logPath).subarray(offset);
      } catch {
        return;
      }
      await testInfo.attach(`server-w${testInfo.workerIndex}.log`, {
        body,
        contentType: "text/plain",
      });
    },
    { auto: true },
  ],

  machineLoadOnFailure: [
    // eslint-disable-next-line no-empty-pattern
    async ({}, use, testInfo) => {
      await use();
      if (testInfo.status === "skipped") return;
      if (testInfo.status === testInfo.expectedStatus) return;
      try {
        testInfo.annotations.push({
          type: "machine-load",
          description: JSON.stringify({
            loadavg: os.loadavg(),
            cpus: os.availableParallelism(),
          }),
        });
      } catch {
        return;
      }
    },
    { auto: true },
  ],
});

export { expect };

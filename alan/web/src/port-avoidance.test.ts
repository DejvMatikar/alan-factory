import { expect, it } from "vitest";

import {
  CANDIDATE_COUNT,
  CANDIDATE_STRIDE,
  PORT_SPAN,
  candidatePorts,
  claimPort,
  firstFreePort,
  holderLine,
  isAlanListener,
  type PortHolder,
  type PortOps,
} from "./port-avoidance";
import { RESTRICTED_PORTS } from "./lib/restricted-ports";

function fakeOps(initialHolders: ReadonlyMap<number, PortHolder[]>): PortOps & {
  readonly reclaimed: number[];
  readonly warnings: string[];
} {
  const holdersByPort = new Map(initialHolders);
  const reclaimed: number[] = [];
  const warnings: string[] = [];
  return {
    holders: (port) => holdersByPort.get(port) ?? [],
    reclaim: (pid) => {
      reclaimed.push(pid);
      for (const [port, holders] of holdersByPort) {
        holdersByPort.set(
          port,
          holders.filter((holder) => holder.pid !== pid),
        );
      }
    },
    warn: (message) => {
      warnings.push(message);
    },
    reclaimed,
    warnings,
  };
}

it("isAlanListener matches a python -m alan command and rejects everything else", () => {
  expect(isAlanListener("/usr/bin/python3.11 -m alan --port 5700")).toBe(true);
  expect(isAlanListener("Rancher")).toBe(false);
  expect(isAlanListener("")).toBe(false);
});

it("holderLine renders freePortOrThrow's exact wording (e2e/fixtures/e2e-server.ts)", () => {
  expect(holderLine(6109, { pid: 5529, command: "Rancher" })).toBe(
    "non-alan process holds 127.0.0.1:6109: PID 5529 Rancher",
  );
});

it.each([0, 999, 409])(
  "candidatePorts stays within [basePort, basePort+999] and starts at the first non-restricted offset (%i)",
  (pid) => {
    const basePort = 5700;
    const ports = candidatePorts(basePort, pid);
    expect(ports).toHaveLength(CANDIDATE_COUNT);
    expect(RESTRICTED_PORTS.has(basePort + pid)).toBe(false);
    expect(ports[0]).toBe(basePort + pid);
    for (const port of ports) {
      expect(port).toBeGreaterThanOrEqual(basePort);
      expect(port).toBeLessThanOrEqual(basePort + PORT_SPAN - 1);
      expect(RESTRICTED_PORTS.has(port)).toBe(false);
    }
    expect(new Set(ports).size).toBe(CANDIDATE_COUNT);
  },
);

it("candidatePorts never returns a restricted port for any pid, across all four in-use bases", () => {
  for (const basePort of [4700, 5700, 5720, 5740]) {
    for (let pid = 0; pid < PORT_SPAN; pid++) {
      const ports = candidatePorts(basePort, pid);
      expect(ports).toHaveLength(CANDIDATE_COUNT);
      expect(new Set(ports).size).toBe(CANDIDATE_COUNT);
      for (const port of ports) {
        expect(RESTRICTED_PORTS.has(port)).toBe(false);
      }
    }
  }
});

it("candidatePorts never returns 5900 for the pid that used to collide with it, across the three audit bases", () => {
  for (const [basePort, pid] of [
    [5700, 200],
    [5720, 180],
    [5740, 160],
  ] as const) {
    expect(candidatePorts(basePort, pid)).not.toContain(5900);
  }
});

it("candidatePorts advances the pid that used to collide with 5900 to 5961", () => {
  expect(candidatePorts(5700, 200)[0]).toBe(5961);
  expect(candidatePorts(5720, 180)[0]).toBe(5961);
  expect(candidatePorts(5740, 160)[0]).toBe(5961);
});

it("RESTRICTED_PORTS excludes only 5900, not its neighbours", () => {
  expect(RESTRICTED_PORTS.has(5899)).toBe(false);
  expect(RESTRICTED_PORTS.has(5901)).toBe(false);
});

it("RESTRICTED_PORTS contains the live-span blocked ports and excludes ordinary ports", () => {
  for (const port of [6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 5060, 5061, 5900]) {
    expect(RESTRICTED_PORTS.has(port)).toBe(true);
  }
  for (const port of [8080, 5700, 4700]) {
    expect(RESTRICTED_PORTS.has(port)).toBe(false);
  }
});

it("CANDIDATE_STRIDE is coprime with PORT_SPAN", () => {
  const gcd = (a: number, b: number): number => (b === 0 ? a : gcd(b, a % b));
  expect(gcd(CANDIDATE_STRIDE, PORT_SPAN)).toBe(1);
});

it("claimPort returns empty immediately when nothing holds the port", () => {
  const ops = fakeOps(new Map());
  expect(claimPort(5700, ops)).toEqual([]);
  expect(ops.reclaimed).toEqual([]);
});

it("claimPort reclaims an alan-owned holder and returns empty once it is gone", () => {
  const holder: PortHolder = { pid: 111, command: "/usr/bin/python3 -m alan" };
  const ops = fakeOps(new Map([[5700, [holder]]]));
  expect(claimPort(5700, ops)).toEqual([]);
  expect(ops.reclaimed).toEqual([111]);
});

it("claimPort re-probes once and reports an alan holder that survives reclaim", () => {
  const holder: PortHolder = { pid: 111, command: "/usr/bin/python3 -m alan" };
  const holdersByPort = new Map([[5700, [holder]]]);
  const reclaimed: number[] = [];
  const ops: PortOps = {
    holders: (port) => holdersByPort.get(port) ?? [],
    reclaim: (pid) => {
      reclaimed.push(pid);
    },
    warn: () => undefined,
  };
  expect(claimPort(5700, ops)).toEqual([holder]);
  expect(reclaimed).toEqual([111]);
});

it("claimPort never reclaims a non-alan holder", () => {
  const holder: PortHolder = { pid: 5529, command: "Rancher" };
  const ops = fakeOps(new Map([[5700, [holder]]]));
  expect(claimPort(5700, ops)).toEqual([holder]);
  expect(ops.reclaimed).toEqual([]);
});

it("firstFreePort returns the first candidate when it is free", () => {
  const ops = fakeOps(new Map());
  const pid = 42;
  const port = firstFreePort(5700, pid, ops);
  expect(port).toBe(5700 + (pid % PORT_SPAN));
  expect(ops.warnings).toEqual([]);
});

it("firstFreePort warns and advances past a foreign holder to the next free candidate", () => {
  const pid = 42;
  const basePort = 5700;
  const [first, second] = candidatePorts(basePort, pid);
  const holder: PortHolder = { pid: 5529, command: "Rancher" };
  const ops = fakeOps(new Map([[first!, [holder]]]));
  const port = firstFreePort(basePort, pid, ops);
  expect(port).toBe(second);
  expect(ops.warnings).toEqual([`ERROR: ${holderLine(first!, holder)}`]);
});

it("firstFreePort advances past several consecutive foreign candidates", () => {
  const pid = 42;
  const basePort = 5700;
  const candidates = candidatePorts(basePort, pid);
  const holderA: PortHolder = { pid: 100, command: "foo" };
  const holderB: PortHolder = { pid: 200, command: "bar" };
  const ops = fakeOps(
    new Map([
      [candidates[0]!, [holderA]],
      [candidates[1]!, [holderB]],
    ]),
  );
  const port = firstFreePort(basePort, pid, ops);
  expect(port).toBe(candidates[2]);
  expect(ops.warnings).toEqual([
    `ERROR: ${holderLine(candidates[0]!, holderA)}`,
    `ERROR: ${holderLine(candidates[1]!, holderB)}`,
  ]);
});

it("firstFreePort treats an empty command string as foreign, not alan-owned", () => {
  const pid = 42;
  const basePort = 5700;
  const candidates = candidatePorts(basePort, pid);
  const holder: PortHolder = { pid: 999, command: "" };
  const ops = fakeOps(new Map([[candidates[0]!, [holder]]]));
  const port = firstFreePort(basePort, pid, ops);
  expect(port).toBe(candidates[1]);
  expect(ops.reclaimed).toEqual([]);
});

it("firstFreePort throws after exhausting every candidate, naming every blocker", () => {
  const pid = 42;
  const basePort = 5700;
  const candidates = candidatePorts(basePort, pid);
  const holdersMap = new Map<number, PortHolder[]>(
    candidates.map((port, i) => [port, [{ pid: 1000 + i, command: `blocker-${i}` }]]),
  );
  const ops = fakeOps(holdersMap);
  let thrown: Error | undefined;
  try {
    firstFreePort(basePort, pid, ops);
  } catch (error) {
    thrown = error as Error;
  }
  expect(thrown).toBeDefined();
  const message = thrown?.message ?? "";
  expect(message).toContain(
    `no free port in ${basePort}-${basePort + PORT_SPAN - 1} after ${CANDIDATE_COUNT} candidates`,
  );
  candidates.forEach((port, i) => {
    expect(message).toContain(`127.0.0.1:${port}`);
    expect(message).toContain(`PID ${1000 + i}`);
    expect(message).toContain(`blocker-${i}`);
  });
});

it("firstFreePort's exhaustion message count matches the extended candidate list when the walk crosses a restricted port", () => {
  const pid = 300;
  const basePort = 5700;
  const candidates = candidatePorts(basePort, pid);
  expect(candidates).not.toContain(basePort + pid);
  const holdersMap = new Map<number, PortHolder[]>(
    candidates.map((port, i) => [port, [{ pid: 2000 + i, command: `blocker-${i}` }]]),
  );
  const ops = fakeOps(holdersMap);
  let thrown: Error | undefined;
  try {
    firstFreePort(basePort, pid, ops);
  } catch (error) {
    thrown = error as Error;
  }
  expect(thrown).toBeDefined();
  expect(thrown?.message ?? "").toContain(
    `no free port in ${basePort}-${basePort + PORT_SPAN - 1} after ${candidates.length} candidates`,
  );
});

import { RESTRICTED_PORTS } from "./lib/restricted-ports.ts";

export interface PortHolder {
  readonly pid: number;
  readonly command: string;
}

export interface PortOps {
  // Built on unprivileged `lsof -t -iTCP:<port> -sTCP:LISTEN` — listeningPids()
  // in e2e/fixtures/e2e-server.ts, called from holdersOf() in
  // e2e/fixtures/port-ops.ts — which is structurally blind to a socket owned
  // by another user — an empty result means "no holder I can see", not
  // "free". RESTRICTED_PORTS is the mitigation for known blind spots (see
  // restricted-ports.ts's 5900 entry), not a fix to this probe. Per
  // docs/architecture/e2e_testing.md's single-seam invariant, the skip
  // belongs in candidatePorts alone — do not special-case a port here or in
  // a teardown path instead.
  readonly holders: (port: number) => readonly PortHolder[];
  readonly reclaim: (pid: number) => void;
  readonly warn: (message: string) => void;
}

export const PORT_SPAN = 1000;
export const CANDIDATE_COUNT = 16;
export const CANDIDATE_STRIDE = 61; // coprime with PORT_SPAN => 16 distinct offsets

export function isAlanListener(command: string): boolean {
  return /python.* -m alan/.test(command);
}

// Wording matches freePortOrThrow's error (e2e/fixtures/e2e-server.ts) exactly
// so CI/log searches and drift-guards can match either job's failure the same way.
export function holderLine(port: number, holder: PortHolder): string {
  return `non-alan process holds 127.0.0.1:${port}: PID ${holder.pid} ${holder.command}`;
}

export function candidatePorts(basePort: number, pid: number): number[] {
  const offset = pid % PORT_SPAN;
  const ports: number[] = [];
  for (let i = 0; ports.length < CANDIDATE_COUNT && i < PORT_SPAN; i++) {
    const port = basePort + ((offset + i * CANDIDATE_STRIDE) % PORT_SPAN);
    if (!RESTRICTED_PORTS.has(port)) {
      ports.push(port);
    }
  }
  return ports;
}

// Reclaims only alan-owned holders; a foreign holder is never touched. If
// anything was reclaimed, re-probes once for the current state instead of
// trusting the pre-reclaim snapshot — a slow-dying process just costs us
// the next candidate, not a sleep.
export function claimPort(port: number, ops: PortOps): readonly PortHolder[] {
  const initial = ops.holders(port);
  const alanHolders = initial.filter((holder) => isAlanListener(holder.command));
  for (const holder of alanHolders) {
    ops.reclaim(holder.pid);
  }
  if (alanHolders.length > 0) {
    return ops.holders(port);
  }
  return initial;
}

export function firstFreePort(basePort: number, pid: number, ops: PortOps): number {
  const blockerLines: string[] = [];
  const candidates = candidatePorts(basePort, pid);
  for (const port of candidates) {
    const remaining = claimPort(port, ops);
    if (remaining.length === 0) {
      return port;
    }
    for (const holder of remaining) {
      const line = `ERROR: ${holderLine(port, holder)}`;
      ops.warn(line);
      blockerLines.push(line);
    }
  }
  throw new Error(
    [
      `no free port in ${basePort}-${basePort + PORT_SPAN - 1} after ${candidates.length} candidates`,
      ...blockerLines,
    ].join("\n"),
  );
}

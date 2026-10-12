import { execFileSync } from "node:child_process";

import { listeningPids } from "./e2e-server";
import type { PortHolder, PortOps } from "../../src/port-avoidance";

export function holdersOf(port: number): PortHolder[] {
  const holders: PortHolder[] = [];
  for (const pidStr of listeningPids(port)) {
    let command = "";
    try {
      command = execFileSync("ps", ["-p", pidStr, "-o", "command="], {
        encoding: "utf8",
      }).trim();
    } catch {
      continue;
    }
    if (!command) continue;
    holders.push({ pid: Number(pidStr), command });
  }
  return holders;
}

export const systemPortOps: PortOps = {
  holders: holdersOf,
  reclaim: (pid) => {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
    }
  },
  warn: (message) => {
    console.warn(message);
  },
};

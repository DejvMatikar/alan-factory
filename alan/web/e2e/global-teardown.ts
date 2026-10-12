import { execFileSync } from "node:child_process";

import { E2E_WORKERS, workerPort } from "./fixtures/server-env";
import { listeningPids } from "./fixtures/e2e-server";

export default function globalTeardown(): void {
  for (let i = 0; i < E2E_WORKERS; i += 1) {
    for (const pid of listeningPids(workerPort(i))) {
      let cmd = "";
      try {
        cmd = execFileSync("ps", ["-p", pid, "-o", "command="], {
          encoding: "utf8",
        }).trim();
      } catch {
        continue;
      }
      if (/python.* -m alan/.test(cmd)) {
        try {
          process.kill(Number(pid), "SIGKILL");
        } catch {
        }
      }
    }
  }
}

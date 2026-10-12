import { holdersOf } from "../fixtures/port-ops";
import { isAlanListener } from "../../src/port-avoidance";
import { resolveDesktopAuditMaxPort, resolveDesktopAuditStandardPort } from "../../src/desktop-audit-config";

function killPort(port: number): void {
  for (const holder of holdersOf(port)) {
    if (isAlanListener(holder.command)) {
      try {
        process.kill(holder.pid, "SIGKILL");
      } catch {
      }
    }
  }
}

export default function globalTeardown(): void {
  killPort(resolveDesktopAuditStandardPort());
  killPort(resolveDesktopAuditMaxPort());
}

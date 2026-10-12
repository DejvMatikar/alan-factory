import { holdersOf } from "../fixtures/port-ops";
import { isAlanListener } from "../../src/port-avoidance";
import { resolveMobileAuditPort } from "../../src/mobile-audit-config";

export default function globalTeardown(): void {
  const port = resolveMobileAuditPort();
  for (const holder of holdersOf(port)) {
    if (isAlanListener(holder.command)) {
      try {
        process.kill(holder.pid, "SIGKILL");
      } catch {
      }
    }
  }
}

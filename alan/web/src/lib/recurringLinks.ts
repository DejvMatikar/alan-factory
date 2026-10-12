import type { RecurringProcess } from "../api";

export type RecurringLinkContext = "latest run" | "configuration";

export type RecurringDestination =
  | { readonly kind: "external"; readonly href: string; readonly linkContext: "latest run" }
  | { readonly kind: "internal"; readonly href: string; readonly linkContext: RecurringLinkContext }
  | {
      readonly kind: "none";
      readonly href: null;
      readonly linkContext: null;
      readonly reason?: string;
    };

const NO_DESTINATION: RecurringDestination = { kind: "none", href: null, linkContext: null };

const SWEEP_PATH: Record<string, string> = {
  triage_sweep: "/health",
  daily_digest: "/health",
};

function splitEngineKey(engineKey: string): { owner: string; repo: string } | null {
  const slashIndex = engineKey.indexOf("/");
  if (slashIndex <= 0 || slashIndex === engineKey.length - 1) return null;
  return { owner: engineKey.slice(0, slashIndex), repo: engineKey.slice(slashIndex + 1) };
}

function systemSweepDestination(
  process: RecurringProcess,
  engineKey: string,
): RecurringDestination {
  const path = SWEEP_PATH[process.process_id];
  if (path != null) return { kind: "internal", href: path, linkContext: "latest run" };
  if (process.process_id === "dependency_sweep") {
    const split = splitEngineKey(engineKey);
    if (split == null) return NO_DESTINATION;
    return {
      kind: "internal",
      href: `/repos/${encodeURIComponent(split.owner)}/${encodeURIComponent(split.repo)}/config`,
      linkContext: "configuration",
    };
  }
  return NO_DESTINATION;
}

export function processDestination(
  process: RecurringProcess,
  engineKey: string,
): RecurringDestination {
  switch (process.family) {
    case "scheduled_ci":
    case "dependency_update":
      return process.last_run.url != null
        ? { kind: "external", href: process.last_run.url, linkContext: "latest run" }
        : NO_DESTINATION;
    case "system_sweep":
      return systemSweepDestination(process, engineKey);
    case "backup":
      return {
        kind: "none",
        href: null,
        linkContext: null,
        reason: "No viewing surface in the product",
      };
  }
}

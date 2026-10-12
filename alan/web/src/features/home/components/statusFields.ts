import { formatActiveDuration } from "../../../lib/format";
import type {
  RepoStatus,
  RepoStatusCost,
  RepoStatusDeliveryTime,
  RepoStatusLastDeploy,
  RepoStatusMainCi,
  RepoStatusOpenPrs,
} from "../../../api";
import type { ChipTone } from "../../../components/AggregateChip";
import { formatUsd } from "./cells";

export interface ChipView {
  readonly tone: ChipTone;
  readonly label: string;
}

// alan/repo_status.py always populates a field's sibling numbers together —
// {green,red}_count, {total,authored_by_alan}, and finding_count are only ever
// null as a pair with an unknown/refreshing provenance, which the checks above
// already route away from these lines. The `!` assertions below name that
// invariant instead of adding an untestable (because backend-unreachable) null
// branch on the frontend.

const COUNTED_OVER_LABEL: Record<NonNullable<RepoStatusMainCi["counted_over"]>, string> = {
  main_ci_workflow: "main CI workflow",
  all_checks: "all checks",
};

export function mainCiChip(field: RepoStatusMainCi): ChipView {
  if (field.provenance.status === "unknown") {
    return { tone: "unknown", label: "CI unknown" };
  }
  if (field.green_count == null) {
    return { tone: "transitional", label: "CI refreshing" };
  }
  const bare = `${String(field.green_count)} green · ${String(field.red_count!)} red`;
  // A counts-bearing record whose scope is absent predates the scoped field, so
  // what those counts were taken over is unknowable here: never resolve it to
  // green, and never interpolate the missing scope into the label.
  if (field.counted_over == null) {
    return { tone: "transitional", label: `${bare} · scope unknown` };
  }
  const scope = COUNTED_OVER_LABEL[field.counted_over];
  if (field.green_count === 0 && field.red_count === 0) {
    return { tone: "transitional", label: `no checks yet · ${scope}` };
  }
  const counts = `${bare} · ${scope}`;
  if (field.provenance.status !== "fresh") {
    return { tone: "transitional", label: `${counts} · stale` };
  }
  return { tone: field.red_count! > 0 ? "red" : "green", label: counts };
}

export function openPrsChip(field: RepoStatusOpenPrs): ChipView {
  if (field.provenance.status === "unknown") {
    return { tone: "unknown", label: "Open PRs unknown" };
  }
  if (field.total == null) {
    return { tone: "transitional", label: "Open PRs refreshing" };
  }
  const counts = `${String(field.total)} open · ${String(field.authored_by_alan!)} by alan`;
  if (field.provenance.status !== "fresh") {
    return { tone: "transitional", label: `${counts} · stale` };
  }
  return { tone: "green", label: counts };
}

export function costChip(field: RepoStatusCost): ChipView {
  if (field.provenance.status === "unknown" || field.total_cost_usd == null) {
    return { tone: "unknown", label: "Cost unknown" };
  }
  const suffix = field.unknown_count > 0 ? ` · ${String(field.unknown_count)} unknown` : "";
  return { tone: "green", label: `${formatUsd(field.total_cost_usd)}${suffix}` };
}

export function deliveryTimeChips(field: RepoStatusDeliveryTime): readonly ChipView[] {
  return field.by_size.map((stat) => {
    if (stat.median_active_seconds == null) {
      return { tone: "unknown", label: `${stat.size} · no data` };
    }
    const duration = formatActiveDuration(stat.median_active_seconds);
    return { tone: "green", label: `${stat.size} · ${duration} · n=${String(stat.delivered_count)}` };
  });
}

export function lastDeployChip(field: RepoStatusLastDeploy): ChipView {
  if (field.provenance.status === "unknown") {
    return { tone: "unknown", label: "Last deploy unknown" };
  }
  const prLabel = field.pr_number == null ? "" : ` · PR #${String(field.pr_number)}`;
  const escalated = field.outcome === "escalate";
  return { tone: escalated ? "red" : "green", label: `${escalated ? "Deploy escalated" : "Deployed"}${prLabel}` };
}

export interface RepoStatusChips {
  readonly mainCi: ChipView;
  readonly openPrs: ChipView;
  readonly cost: ChipView;
  readonly deliveryTime: readonly ChipView[];
  readonly lastDeploy: ChipView;
}

export function repoStatusChips(status: RepoStatus): RepoStatusChips {
  return {
    mainCi: mainCiChip(status.main_ci),
    openPrs: openPrsChip(status.open_prs),
    cost: costChip(status.cost),
    deliveryTime: deliveryTimeChips(status.delivery_time),
    lastDeploy: lastDeployChip(status.last_deploy),
  };
}

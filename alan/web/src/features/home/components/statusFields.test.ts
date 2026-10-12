import { describe, expect, it } from "vitest";

import type {
  RepoStatus,
  RepoStatusCost,
  RepoStatusDeliveryTime,
  RepoStatusLastDeploy,
  RepoStatusOpenPrs,
  RepoStatusProvenance,
} from "../../../api";
import {
  costChip,
  deliveryTimeChips,
  lastDeployChip,
  mainCiChip,
  openPrsChip,
  repoStatusChips,
} from "./statusFields";

function provenance(overrides: Partial<RepoStatusProvenance> = {}): RepoStatusProvenance {
  return { status: "fresh", detail: "", ...overrides };
}

describe("mainCiChip", () => {
  it("renders unknown when the provenance is unknown", () => {
    expect(mainCiChip({ provenance: provenance({ status: "unknown" }), green_count: null, red_count: null, counted_over: null })).toEqual({
      tone: "unknown",
      label: "CI unknown",
    });
  });

  it("renders transitional with no counts while refreshing with no cache yet", () => {
    const chip = mainCiChip({
      provenance: provenance({ status: "refreshing" }),
      green_count: null,
      red_count: null,
      counted_over: null,
    });
    expect(chip).toEqual({ tone: "transitional", label: "CI refreshing" });
  });

  it("keeps a stale field's last-known counts, marked stale, instead of red", () => {
    const chip = mainCiChip({
      provenance: provenance({ status: "stale" }),
      green_count: 2,
      red_count: 1,
      counted_over: "main_ci_workflow",
    });
    expect(chip).toEqual({
      tone: "transitional",
      label: "2 green · 1 red · main CI workflow · stale",
    });
  });

  it("renders red when a fresh field has at least one failing check", () => {
    const chip = mainCiChip({
      provenance: provenance(),
      green_count: 2,
      red_count: 1,
      counted_over: "main_ci_workflow",
    });
    expect(chip).toEqual({ tone: "red", label: "2 green · 1 red · main CI workflow" });
  });

  it("renders green when a fresh field has zero failing checks", () => {
    const chip = mainCiChip({
      provenance: provenance(),
      green_count: 3,
      red_count: 0,
      counted_over: "main_ci_workflow",
    });
    expect(chip).toEqual({ tone: "green", label: "3 green · 0 red · main CI workflow" });
  });

  it("names the all-checks fallback scope when the engine declares no main CI workflow", () => {
    const chip = mainCiChip({
      provenance: provenance(),
      green_count: 3,
      red_count: 1,
      counted_over: "all_checks",
    });
    expect(chip).toEqual({ tone: "red", label: "3 green · 1 red · all checks" });
  });

  it("refuses to resolve a counts-bearing record with no scope to green", () => {
    const chip = mainCiChip({
      provenance: provenance(),
      green_count: 3,
      red_count: 0,
      counted_over: null,
    });
    expect(chip).toEqual({ tone: "transitional", label: "3 green · 0 red · scope unknown" });
  });

  it("renders transitional, not green, when the head commit has no counted checks yet", () => {
    const chip = mainCiChip({
      provenance: provenance(),
      green_count: 0,
      red_count: 0,
      counted_over: "main_ci_workflow",
    });
    expect(chip).toEqual({ tone: "transitional", label: "no checks yet · main CI workflow" });
  });
});

describe("openPrsChip", () => {
  const unknownField: RepoStatusOpenPrs = {
    provenance: provenance({ status: "unknown" }),
    total: null,
    authored_by_alan: null,
  };

  it("renders unknown when the provenance is unknown", () => {
    expect(openPrsChip(unknownField)).toEqual({ tone: "unknown", label: "Open PRs unknown" });
  });

  it("renders transitional with no counts while refreshing with no cache yet", () => {
    const chip = openPrsChip({ provenance: provenance({ status: "refreshing" }), total: null, authored_by_alan: null });
    expect(chip).toEqual({ tone: "transitional", label: "Open PRs refreshing" });
  });

  it("keeps a stale field's last-known counts, marked stale", () => {
    const chip = openPrsChip({ provenance: provenance({ status: "stale" }), total: 4, authored_by_alan: 2 });
    expect(chip).toEqual({ tone: "transitional", label: "4 open · 2 by alan · stale" });
  });

  it("renders green for a fresh count — open PRs carry no failure semantic", () => {
    const chip = openPrsChip({ provenance: provenance(), total: 5, authored_by_alan: 1 });
    expect(chip).toEqual({ tone: "green", label: "5 open · 1 by alan" });
  });
});

describe("costChip", () => {
  it("renders unknown when the provenance is unknown", () => {
    const field: RepoStatusCost = {
      provenance: provenance({ status: "unknown" }),
      total_cost_usd: null,
      unknown_count: 0,
    };
    expect(costChip(field)).toEqual({ tone: "unknown", label: "Cost unknown" });
  });

  it("renders unknown when total_cost_usd is null even if provenance claims fresh", () => {
    const field: RepoStatusCost = {
      provenance: provenance(),
      total_cost_usd: null,
      unknown_count: 0,
    };
    expect(costChip(field)).toEqual({ tone: "unknown", label: "Cost unknown" });
  });

  it("renders the plain total with no suffix when nothing is unknown", () => {
    const field: RepoStatusCost = {
      provenance: provenance(),
      total_cost_usd: 120,
      unknown_count: 0,
    };
    expect(costChip(field)).toEqual({ tone: "green", label: "$120.00" });
  });

  it("appends an unknown-count suffix so the total reads as a floor, not a total", () => {
    const field: RepoStatusCost = {
      provenance: provenance(),
      total_cost_usd: 40,
      unknown_count: 3,
    };
    expect(costChip(field)).toEqual({ tone: "green", label: "$40.00 · 3 unknown" });
  });
});

describe("deliveryTimeChips", () => {
  it("renders an explicit no-data chip, never a fabricated 0s, for an empty bucket", () => {
    const field: RepoStatusDeliveryTime = {
      provenance: provenance(),
      by_size: [{ size: "S", median_active_seconds: null, delivered_count: 0 }],
    };
    expect(deliveryTimeChips(field)).toEqual([{ tone: "unknown", label: "S · no data" }]);
  });

  it("renders the median duration and sample size for a populated bucket", () => {
    const field: RepoStatusDeliveryTime = {
      provenance: provenance(),
      by_size: [{ size: "M", median_active_seconds: 3600, delivered_count: 5 }],
    };
    expect(deliveryTimeChips(field)).toEqual([{ tone: "green", label: "M · 1h 0m · n=5" }]);
  });

  it("maps every by_size entry in wire order", () => {
    const field: RepoStatusDeliveryTime = {
      provenance: provenance(),
      by_size: [
        { size: "XS", median_active_seconds: 60, delivered_count: 1 },
        { size: "S", median_active_seconds: null, delivered_count: 0 },
      ],
    };
    expect(deliveryTimeChips(field).map((c) => c.label)).toEqual(["XS · 1m · n=1", "S · no data"]);
  });
});

describe("lastDeployChip", () => {
  it("renders unknown when the provenance is unknown (non-default engine)", () => {
    const field: RepoStatusLastDeploy = {
      provenance: provenance({ status: "unknown" }),
      outcome: null,
      pr_number: null,
      at: null,
    };
    expect(lastDeployChip(field)).toEqual({ tone: "unknown", label: "Last deploy unknown" });
  });

  it("renders red with the PR number for an escalated deploy", () => {
    const field: RepoStatusLastDeploy = {
      provenance: provenance(),
      outcome: "escalate",
      pr_number: 42,
      at: "2026-07-28T00:00:00Z",
    };
    expect(lastDeployChip(field)).toEqual({ tone: "red", label: "Deploy escalated · PR #42" });
  });

  it("renders green with no PR suffix when the PR number is absent", () => {
    const field: RepoStatusLastDeploy = {
      provenance: provenance(),
      outcome: "confirmed",
      pr_number: null,
      at: "2026-07-28T00:00:00Z",
    };
    expect(lastDeployChip(field)).toEqual({ tone: "green", label: "Deployed" });
  });
});

describe("repoStatusChips", () => {
  it("maps every field of a full RepoStatus into its own chip view", () => {
    const status: RepoStatus = {
      slug: "acme/widget",
      is_default: false,
      main_ci: { provenance: provenance({ status: "unknown" }), green_count: null, red_count: null, counted_over: null },
      open_prs: { provenance: provenance({ status: "unknown" }), total: null, authored_by_alan: null },
      cost: { provenance: provenance({ status: "unknown" }), total_cost_usd: null, unknown_count: 0 },
      delivery_time: {
        provenance: provenance({ status: "unknown" }),
        by_size: [{ size: "M", median_active_seconds: null, delivered_count: 0 }],
      },
      last_deploy: { provenance: provenance({ status: "unknown" }), outcome: null, pr_number: null, at: null },
      health: { provenance: provenance({ status: "unknown" }), finding_count: null, verdict: null },
    };
    const chips = repoStatusChips(status);
    expect(chips.mainCi.tone).toBe("unknown");
    expect(chips.openPrs.tone).toBe("unknown");
    expect(chips.cost.tone).toBe("unknown");
    expect(chips.deliveryTime).toEqual([{ tone: "unknown", label: "M · no data" }]);
    expect(chips.lastDeploy.tone).toBe("unknown");
  });
});

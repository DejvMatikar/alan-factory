import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { HealthFinding } from "../../../api";
import { settleDefaultEngineKey } from "../../../test/engineFixtures";
import FindingsTable from "./FindingsTable";

function finding(overrides: Partial<HealthFinding> = {}): HealthFinding {
  return {
    finding_id: "task-123",
    kind: "build_task",
    severity: "critical",
    description: "Build task task-123 has failing CI checks (PR #42)",
    workspace_id: "task-123",
    pr_number: 42,
    blocker_flags: ["pr_checks_failing"],
    action: "dispatch_remediation",
    engine_key: "DejvMatikar/alan",
    in_flight: false,
    ...overrides,
  };
}

function renderTable(findings: HealthFinding[], hasNonDefaultEngine = false) {
  return render(
    <MemoryRouter>
      <FindingsTable findings={findings} hasNonDefaultEngine={hasNonDefaultEngine} />
    </MemoryRouter>,
  );
}

const _DEGRADED_FINDINGS: HealthFinding[] = [
  finding(),
  finding({
    finding_id: "task-456",
    severity: "medium",
    description: "Build task task-456 is awaiting human input",
    workspace_id: "task-456",
    pr_number: null,
    blocker_flags: ["ws_stuck"],
    action: "deep_link",
    engine_key: null,
  }),
];

describe("FindingsTable", () => {
  it("renders a row per finding", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getByTestId("finding-row-task-123")).toBeInTheDocument();
    expect(screen.getByTestId("finding-row-task-456")).toBeInTheDocument();
  });

  it("shows finding description in row", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(
      screen.getByText("Build task task-123 has failing CI checks (PR #42)"),
    ).toBeInTheDocument();
  });

  it("shows workspace link for build_task findings", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getByTestId("finding-workspace-link-task-123")).toBeInTheDocument();
    expect(screen.getByTestId("finding-workspace-link-task-123").className).toBe(
      "text-btn-action underline",
    );
  });

  it("shows PR link when pr_number is set", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getByTestId("finding-pr-link-task-123")).toBeInTheDocument();
    expect(screen.getByTestId("finding-pr-link-task-123").className).toBe(
      "text-btn-action underline",
    );
  });

  it("shows no PR link when pr_number is null", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.queryByTestId("finding-pr-link-task-456")).not.toBeInTheDocument();
  });

  it("resolves the PR link against a finding's own non-default repo", () => {
    renderTable([
      finding({
        finding_id: "task-sprocket",
        description: "Sprocket engine finding",
        workspace_id: "task-sprocket",
        pr_number: 9,
        blocker_flags: [],
        engine_key: "acme-corp/sprocket",
      }),
    ]);
    expect(screen.getByTestId("finding-pr-link-task-sprocket")).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/pull/9",
    );
  });

  it("renders a degraded, non-clickable PR reference when a finding carries no repo", () => {
    renderTable([
      finding({
        finding_id: "task-unresolved",
        description: "No repo on record",
        workspace_id: "task-unresolved",
        pr_number: 5,
        blocker_flags: [],
        engine_key: null,
      }),
    ]);
    const el = screen.getByTestId("finding-pr-link-task-unresolved");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("PR #5");
    expect(el).toHaveTextContent("repository unresolved");
  });

  it("renders unknown severity with fallback badge class", () => {
    renderTable([
      finding({
        finding_id: "task-unknown-sev",
        severity: "unknown_severity" as HealthFinding["severity"],
        description: "Unknown severity finding",
        workspace_id: "task-unknown-sev",
        pr_number: null,
        blocker_flags: ["ws_stuck"],
        action: "deep_link",
        engine_key: null,
      }),
    ]);
    expect(screen.getByTestId("finding-row-task-unknown-sev")).toBeInTheDocument();
    expect(screen.getByTestId("severity-badge-task-unknown-sev").className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-neutral-tint/15 text-status-neutral-fg",
    );
  });

  it("renders the severity badge with the dark-theme recipe, lowercase and unbolded", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getByTestId("severity-badge-task-123").textContent).toBe("critical");
    expect(screen.getByTestId("severity-badge-task-123").className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-status-danger-tint/15 text-status-danger-fg",
    );
  });

  it("shows category badge 'Build task' for build_task kind", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getByTestId("category-badge-task-123").textContent).toBe("Build task");
    expect(screen.getByTestId("category-badge-task-123").className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-blue-500/15 text-blue-300",
    );
  });

  it.each([
    ["build_task", "Build task"],
    ["main_ci", "Main CI"],
    ["deploy", "Deploy"],
    ["recurring_errors", "Recurring errors"],
    ["planner_behavioral_regression", "Planner regression"],
    ["retention_near_cap", "Retention near cap"],
    ["stale_job", "Stale job"],
    ["finisher_regression", "Finisher regression"],
    ["worker_pool", "Worker pool"],
    ["stale_live_process", "Stale live process"],
    ["config_quarantine", "Config quarantined"],
    ["host_toolchain", "Host toolchain"],
  ] as const)(
    "labels the %s finding kind as '%s', never the raw kind",
    (kind, label) => {
      renderTable([
        finding({
          finding_id: `task-kind-${kind}`,
          kind,
          description: "kind label coverage",
          workspace_id: null,
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: null,
        }),
      ]);
      expect(screen.getByTestId(`category-badge-task-kind-${kind}`).textContent).toBe(label);
      expect(screen.getByTestId(`category-badge-task-kind-${kind}`).textContent).not.toBe(kind);
    },
  );

  it("shows verbatim kind text for unknown category", () => {
    renderTable([
      finding({
        finding_id: "task-unknown-kind",
        kind: "some_future_kind",
        description: "Unknown kind finding",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        engine_key: null,
      }),
    ]);
    expect(screen.getByTestId("category-badge-task-unknown-kind").textContent).toBe(
      "some_future_kind",
    );
  });

  it("shows repo badge for a main_ci finding with repo set", () => {
    renderTable([
      finding({
        finding_id: "main_ci:acme/widget",
        kind: "main_ci",
        description: "Main branch CI is failing (failure)",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        engine_key: null,
        repo: "acme/widget",
      }),
    ]);
    const badge = screen.getByTestId("repo-badge-main_ci:acme/widget");
    expect(badge.textContent).toBe("acme/widget");
    expect(badge.className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-scope-repo-tint/15 text-scope-repo-fg",
    );
  });

  it("shows repo badge for a deploy finding with repo set", () => {
    renderTable([
      finding({
        finding_id: "deploy:acme/widget",
        kind: "deploy",
        description: "Main branch deploy is failing (failure)",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        engine_key: null,
        repo: "acme/widget",
      }),
    ]);
    expect(screen.getByTestId("repo-badge-deploy:acme/widget")).toBeInTheDocument();
  });

  it("shows no repo badge when repo is null", () => {
    renderTable([
      finding({
        finding_id: "main_ci:acme/widget",
        kind: "main_ci",
        description: "Main branch CI is failing (failure)",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        engine_key: null,
        repo: null,
      }),
    ]);
    expect(screen.queryByTestId("repo-badge-main_ci:acme/widget")).not.toBeInTheDocument();
  });

  it("shows no repo badge for a build_task finding even when repo is set", () => {
    renderTable([
      finding({
        finding_id: "task-with-repo",
        severity: "medium",
        description: "Build task task-with-repo is awaiting human input",
        pr_number: null,
        blocker_flags: [],
        action: "deep_link",
        engine_key: null,
        repo: "acme/widget",
      }),
    ]);
    expect(screen.queryByTestId("repo-badge-task-with-repo")).not.toBeInTheDocument();
  });

  it("shows engine badge for a workspace-linked finding when a non-default engine is registered", () => {
    settleDefaultEngineKey(null);
    renderTable(
      [
        finding({
          finding_id: "task-engine",
          severity: "medium",
          description: "Build task task-engine is awaiting human input",
          workspace_id: "task-engine",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: "sprocket-co/sprocket",
        }),
      ],
      true,
    );
    expect(screen.getByTestId("engine-badge-task-engine")).toBeInTheDocument();
    expect(screen.getByTestId("engine-badge-task-engine").textContent).toBe("sprocket-co/sprocket");
  });

  it("hides engine badge when no non-default engine is registered, even with engine_key set", () => {
    renderTable(
      [
        finding({
          finding_id: "task-engine-2",
          severity: "medium",
          description: "Build task task-engine-2 is awaiting human input",
          workspace_id: "task-engine-2",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: "sprocket-co/sprocket",
        }),
      ],
      false,
    );
    expect(screen.queryByTestId("engine-badge-task-engine-2")).not.toBeInTheDocument();
  });

  it("hides engine badge when engine_key is null even with a non-default engine registered", () => {
    renderTable(
      [
        finding({
          finding_id: "task-no-engine",
          severity: "medium",
          description: "Build task task-no-engine is awaiting human input",
          workspace_id: "task-no-engine",
          pr_number: null,
          blocker_flags: [],
          action: "deep_link",
          engine_key: null,
        }),
      ],
      true,
    );
    expect(screen.queryByTestId("engine-badge-task-no-engine")).not.toBeInTheDocument();
  });

  it("renders blocker chips for finding with non-empty blocker_flags", () => {
    renderTable(_DEGRADED_FINDINGS);
    const container = screen.getByTestId("blocker-flags-task-123");
    expect(container).toBeInTheDocument();
    expect(container.textContent).toContain("pr_checks_failing");
    const chip = container.querySelector("span");
    expect(chip?.className).toBe(
      "inline-block rounded px-2 py-0.5 text-xs bg-amber-500/15 text-amber-300",
    );
  });

  it("renders two separate chips when blocker_flags has two entries", () => {
    renderTable([
      finding({
        finding_id: "task-twochips",
        description: "Two blocker flags",
        workspace_id: "task-twochips",
        pr_number: null,
        blocker_flags: ["pr_checks_failing", "job_failed"],
        engine_key: null,
      }),
    ]);
    const container = screen.getByTestId("blocker-flags-task-twochips");
    expect(container.querySelectorAll("span")).toHaveLength(2);
    expect(container.textContent).toContain("pr_checks_failing");
    expect(container.textContent).toContain("job_failed");
  });

  it("renders no blocker chips container for finding with empty blocker_flags", () => {
    renderTable([
      finding({
        finding_id: "task-no-flags",
        severity: "medium",
        description: "No blocker flags here",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        engine_key: null,
      }),
    ]);
    expect(screen.queryByTestId("blocker-flags-task-no-flags")).not.toBeInTheDocument();
  });

  it("shows in-progress chip when in_flight is true", () => {
    renderTable([
      finding({
        finding_id: "task-inflight",
        description: "Remediation already running",
        workspace_id: "task-inflight",
        pr_number: null,
        blocker_flags: ["job_failed"],
        in_flight: true,
        engine_key: null,
      }),
    ]);
    expect(screen.getByTestId("in-progress-chip-task-inflight")).toBeInTheDocument();
  });

  it("shows no in-progress chip when in_flight is false", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.queryByTestId("in-progress-chip-task-123")).not.toBeInTheDocument();
  });

  it("shows an observed-at chip when the finding carries observed_at", () => {
    renderTable([
      finding({
        finding_id: "worker_pool",
        kind: "worker_pool",
        description: "Dispatch loop threads: 0/2 live",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        action: "deep_link",
        engine_key: null,
        observed_at: new Date().toISOString(),
      }),
    ]);
    expect(screen.getByTestId("aggregate-chip-observed-worker_pool")).toBeInTheDocument();
    expect(screen.getByTestId("aggregate-chip-observed-worker_pool").textContent).toContain(
      "as of",
    );
  });

  it("shows no observed-at chip when observed_at is absent", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.queryByTestId("aggregate-chip-observed-task-123")).not.toBeInTheDocument();
  });

  it("shows no observed-at chip when observed_at is null", () => {
    renderTable([
      finding({
        finding_id: "worker_pool",
        kind: "worker_pool",
        description: "Dispatch loop threads: 0/2 live",
        workspace_id: null,
        pr_number: null,
        blocker_flags: [],
        action: "deep_link",
        engine_key: null,
        observed_at: null,
      }),
    ]);
    expect(screen.queryByTestId("aggregate-chip-observed-worker_pool")).not.toBeInTheDocument();
  });

  it("renders exactly the severity, description and links columns and no interactive control", () => {
    renderTable(_DEGRADED_FINDINGS);
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Severity",
      "Description",
      "Links",
    ]);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});

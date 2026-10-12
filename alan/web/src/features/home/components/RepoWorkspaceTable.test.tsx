import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectListEntry, UnblockRecord, WorkspaceSummary } from "../../../api";
import { server } from "../../../test/server";
import { makeWorkspaceSummary } from "../../../test/workspaceFixtures";
import RepoWorkspaceTable, { type RepoWorkspaceTableProps } from "./RepoWorkspaceTable";
import type { RepoSectionModel } from "../../../lib/fold";

function ws(overrides: Partial<WorkspaceSummary> & Pick<WorkspaceSummary, "task_id">): WorkspaceSummary {
  return makeWorkspaceSummary({ title: overrides.task_id, project_id: null, ...overrides });
}

function project(overrides: Partial<ProjectListEntry> & Pick<ProjectListEntry, "id">): ProjectListEntry {
  return {
    title: overrides.id,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    workspace_count: 0,
    total_spend_usd: 0,
    roadmap_items_total: 0,
    roadmap_items_delivered: 0,
    ...overrides,
  };
}

const EMPTY_SECTION: RepoSectionModel = {
  key: "acme/sprocket",
  title: "Sprocket",
  posture: "autonomous",
  projectGroups: [],
  unassigned: [],
  shippedCount: 0,
  archivedCount: 0,
};

function renderTable(section: RepoSectionModel, overrides: Partial<RepoWorkspaceTableProps> = {}) {
  const props: RepoWorkspaceTableProps = {
    section,
    tab: "active",
    reload: vi.fn(),
    showProjects: true,
    loading: false,
    ...overrides,
  };
  return render(
    <MemoryRouter>
      <RepoWorkspaceTable {...props} />
    </MemoryRouter>,
  );
}

describe("RepoWorkspaceTable — loading and empty", () => {
  it("renders the loading register and no table or empty message while loading", () => {
    renderTable(EMPTY_SECTION, { loading: true });
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-empty-acme/sprocket")).not.toBeInTheDocument();
  });

  it("renders an inline one-line empty message typed by the active tab when the section is empty", () => {
    renderTable(EMPTY_SECTION);
    expect(screen.getByTestId("repo-empty-acme/sprocket")).toHaveTextContent("No active workspaces yet.");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loading")).not.toBeInTheDocument();
  });

  it("renders an inline one-line empty message typed by the archived tab when the section is empty", () => {
    renderTable(EMPTY_SECTION, { tab: "archived" });
    expect(screen.getByTestId("repo-empty-acme/sprocket")).toHaveTextContent("No archived workspaces yet.");
  });
});

describe("RepoWorkspaceTable — rows", () => {
  it("renders a project row with title link, phase and done fraction when a roadmap is imported", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p1", title: "Alpha", total_spend_usd: 4, roadmap_items_total: 6, roadmap_items_delivered: 2 }),
          workspaces: [],
        },
      ],
    };
    renderTable(section);
    const row = screen.getByTestId("home-row-project-p1");
    expect(within(row).getByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/projects/p1");
    expect(row).toHaveTextContent("roadmap · 2 of 6 items");
    expect(row).toHaveTextContent("2/6");
    expect(row).toHaveTextContent("$4.00");
  });

  it("renders the column header row with the Active column between Done and Cost, and a leading visible Actions header", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-hdr" })] };
    renderTable(section);
    const headers = screen.getByRole("table").querySelectorAll("thead th");
    const labels = Array.from(headers).map((h) => h.textContent);
    expect(labels).toContain("Active");
    expect(labels.indexOf("Active")).toBeGreaterThan(labels.indexOf("Done"));
    expect(labels.indexOf("Active")).toBeLessThan(labels.indexOf("Cost"));
    expect(labels[0]).toBe("Actions");
    expect(headers[0]?.querySelector(".sr-only")).toBeNull();
  });

  it("keeps every column header fixed via sticky top-0 while the table scrolls beneath it", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-sticky" })] };
    renderTable(section);
    const headers = screen.getByRole("table").querySelectorAll("thead th");
    for (const header of headers) {
      expect(header.className).toContain("sticky");
      expect(header.className).toContain("top-0");
    }
  });

  it("keeps header and body cell counts equal for every row kind", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p-parity" }), workspaces: [ws({ task_id: "w-parity", project_id: "p-parity" })] }],
      unassigned: [ws({ task_id: "u-parity" })],
    };
    renderTable(section);
    const headerCount = screen.getByRole("table").querySelectorAll("thead th").length;
    expect(screen.getByTestId("home-row-project-p-parity").querySelectorAll("td")).toHaveLength(headerCount);
    expect(screen.getByTestId("home-row-feature-w-parity").querySelectorAll("td")).toHaveLength(headerCount);
    expect(screen.getByTestId("home-row-unassigned-acme/sprocket").querySelectorAll("td")).toHaveLength(headerCount);
    expect(screen.getByTestId("home-row-feature-u-parity").querySelectorAll("td")).toHaveLength(headerCount);
  });

  it("renders a feature row's own Active cell from its active_seconds, with an explicit empty state when unset", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({ task_id: "w-with-active", active_seconds: 300 }),
        ws({ task_id: "w-no-active" }),
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("active-time-w-with-active")).toHaveTextContent("5m");
    expect(screen.getByTestId("active-time-w-no-active")).toHaveTextContent("—");
  });

  it("renders a plain dash, not an aggregate, on the Unassigned group header's Active cell", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "u-active", active_seconds: 60 })] };
    renderTable(section);
    const header = screen.getByTestId("home-row-unassigned-acme/sprocket");
    expect(header).toHaveTextContent("—");
    expect(header.querySelector('[data-testid^="active-time-"]')).toBeNull();
  });

  it("shows a project row's Active cell as a Σ-prefixed sum of its workspaces' active_seconds", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p-active" }),
          workspaces: [
            ws({ task_id: "w1", project_id: "p-active", active_seconds: 60 }),
            ws({ task_id: "w2", project_id: "p-active", active_seconds: 120 }),
          ],
        },
      ],
    };
    renderTable(section);
    const cell = screen.getByTestId("active-time-project-p-active");
    expect(cell).toHaveTextContent("Σ 3m");
    expect(cell.getAttribute("aria-label")).toContain("Sum of active fleet time 3m");
  });

  it("shows an explicit empty state on a project row's Active cell when no child workspace has recorded time", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p-noactive" }), workspaces: [ws({ task_id: "w1", project_id: "p-noactive" })] }],
    };
    renderTable(section);
    expect(screen.getByTestId("active-time-project-p-noactive")).toHaveTextContent("Σ —");
  });

  it("renders the planning degradation with an em dash Done cell when a project has no roadmap", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p2", title: "Beta" }), workspaces: [] }],
    };
    renderTable(section);
    const row = screen.getByTestId("home-row-project-p2");
    expect(row).toHaveTextContent("planning · no plan yet");
    expect(row).toHaveTextContent("—");
  });

  it("derives the project row's aggregate state from its own child workspaces", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        { project: project({ id: "p3" }), workspaces: [ws({ task_id: "w1", status: "awaiting_input", project_id: "p3" })] },
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("aggregate-chip-project-p3")).toHaveTextContent("needs you");
  });

  it("nests feature rows under their project with an em dash Phase cell and its own workspace status", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p4" }),
          workspaces: [
            ws({ task_id: "w-feat", title: "My Feature", status: "escalated", project_id: "p4", tasks_total: 3, tasks_done: 1, total_cost_usd: 1.25 }),
          ],
        },
      ],
    };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-w-feat");
    expect(within(row).getByRole("link", { name: /w-feat · My Feature/ })).toHaveAttribute(
      "href",
      "/workspaces/w-feat",
    );
    expect(row).toHaveTextContent("1/3");
    expect(row).toHaveTextContent("$1.25");
    expect(screen.getByTestId("status-escalated")).toBeInTheDocument();
    const phaseCell = row.querySelectorAll("td")[4];
    expect(phaseCell).toHaveTextContent("—");
  });

  it("shows the done fraction, not a PR pill, for a completed one-task feature with a PR", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-pr", tasks_total: 1, tasks_done: 1, pr_number: 99 })] };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-w-pr");
    expect(row).toHaveTextContent("1/1");
    expect(row).not.toHaveTextContent(/PR #/);
  });

  it("shows the not-done fraction for a one-task feature with no PR yet", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-nopr", tasks_total: 1, tasks_done: 0, pr_number: null })] };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-w-nopr");
    expect(row).toHaveTextContent("0/1");
    expect(row).not.toHaveTextContent(/PR #/);
  });

  it("renders an Unassigned group header row followed by its feature rows", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "u1" })] };
    renderTable(section);
    expect(screen.getByTestId("home-row-unassigned-acme/sprocket")).toHaveTextContent("Unassigned");
    expect(screen.getByTestId("home-row-feature-u1")).toBeInTheDocument();
  });

  it("renders no Unassigned group row when there are no project-less workspaces", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, projectGroups: [{ project: project({ id: "p5" }), workspaces: [] }] };
    renderTable(section);
    expect(screen.queryByTestId("home-row-unassigned-acme/sprocket")).not.toBeInTheDocument();
  });

  it("renders the Current team cell with team name and node status badge when present", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-team", latest_team: "research", latest_invocation_status: "running" })] };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-w-team");
    expect(row).toHaveTextContent("research");
    expect(within(row).getByTestId("node-status-running")).toBeInTheDocument();
  });

  it("maps an escalated latest_invocation_status onto the failed node-status badge", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-esc-team", latest_team: "implementation", latest_invocation_status: "escalated" })] };
    renderTable(section);
    expect(within(screen.getByTestId("home-row-feature-w-esc-team")).getByTestId("node-status-failed")).toBeInTheDocument();
  });

  it("renders an em dash for the Team cell when no latest team is recorded", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w-noteam", latest_team: null, latest_invocation_status: null })] };
    renderTable(section);
    expect(screen.getByTestId("home-row-feature-w-noteam")).toHaveTextContent("—");
  });

  it("renders the Auto dependency update badge on a dependency_sweep feature row", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "dep-1", team: "dependency_sweep" })] };
    renderTable(section);
    expect(screen.getByTestId("dep-badge-dep-1")).toHaveTextContent("Auto dependency update");
  });

  it("does not render the dependency badge for a regular team", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "reg-1", team: "implementation" })] };
    renderTable(section);
    expect(screen.queryByTestId("dep-badge-reg-1")).not.toBeInTheDocument();
  });

  it("asserts the absence of any phase rail, meter, checkpoint chip, plan-approved or fingerprint markup", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p7" }), workspaces: [ws({ task_id: "w7", project_id: "p7" })] }],
    };
    const { container } = renderTable(section);
    expect(container.querySelector('[data-testid*="phase-rail"]')).toBeNull();
    expect(container.querySelector('[data-testid*="meter"]')).toBeNull();
    expect(container.querySelector('[data-testid*="checkpoint"]')).toBeNull();
    expect(screen.queryByText(/plan approved/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/fingerprint/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/checkpoints answered/i)).not.toBeInTheDocument();
  });

  it("keeps the project total at two decimals for a fractional-cent aggregate", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p-frac", total_spend_usd: 0.0034 }), workspaces: [] }],
    };
    renderTable(section);
    expect(screen.getByTestId("home-row-project-p-frac")).toHaveTextContent("$0.00");
  });
});

function unblockRecord(overrides: Partial<UnblockRecord> = {}): UnblockRecord {
  return {
    shape: "stale_review",
    attempts: 1,
    ceiling: 5,
    outcome: "not_yet",
    summary: "waiting · stale_review · re-probing · 2026-09-19T11:55Z",
    issue_url: null,
    at: Date.now() / 1000,
    action: null,
    reason: null,
    authority_holder: null,
    ...overrides,
  };
}

describe("RepoWorkspaceTable — unblocker host-owned badge", () => {
  it("shows a host-owned badge for a park the host is waiting on", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({ task_id: "host-waiting", status: "awaiting_input", unblock: unblockRecord({ outcome: "not_yet" }) }),
      ],
    };
    renderTable(section);
    const badge = screen.getByTestId("unblock-owner-badge-host-waiting");
    expect(badge).toHaveTextContent("Host is on it");
    expect(badge.className).toContain("status-active-tint");
  });

  it("shows a distinct label for a park the host has acted on", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({ task_id: "host-acted", status: "escalated", unblock: unblockRecord({ outcome: "acted" }) }),
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("unblock-owner-badge-host-acted")).toHaveTextContent("Host acted");
  });

  it("shows no host-owned badge once the park has been reported as needing a human", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({
          task_id: "needs-human",
          status: "awaiting_input",
          unblock: unblockRecord({ outcome: "needs_human" }),
        }),
      ],
    };
    renderTable(section);
    expect(screen.queryByTestId("unblock-owner-badge-needs-human")).not.toBeInTheDocument();
  });

  it("shows no host-owned badge for a workspace with no unblock record", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: "plain", status: "awaiting_input" })],
    };
    renderTable(section);
    expect(screen.queryByTestId("unblock-owner-badge-plain")).not.toBeInTheDocument();
  });
});

describe("RepoWorkspaceTable — per-row state cell respects host ownership", () => {
  it("loses the attention tint on a host-owned park, showing the in-progress register instead", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({ task_id: "row-host", status: "awaiting_input", unblock: unblockRecord({ outcome: "not_yet" }) }),
      ],
    };
    renderTable(section);
    expect(screen.queryByTestId("status-awaiting_input")).not.toBeInTheDocument();
    const cell = screen.getByTestId("status-host-owned");
    expect(cell.className).toContain("status-active-tint");
    expect(cell.className).not.toContain("status-attention-tint");
  });

  it("keeps the attention tint on a park the host has reported as needing a human", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({
          task_id: "row-needs-human",
          status: "awaiting_input",
          unblock: unblockRecord({ outcome: "needs_human" }),
        }),
      ],
    };
    renderTable(section);
    expect(screen.queryByTestId("status-host-owned")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-awaiting_input").className).toContain("status-attention-tint");
  });

  it("keeps the attention tint on a host-owned park silent past the 90-minute threshold", () => {
    const staleAt = Date.now() / 1000 - 95 * 60;
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({
          task_id: "row-silent",
          status: "awaiting_input",
          unblock: unblockRecord({ outcome: "not_yet", at: staleAt }),
        }),
      ],
    };
    renderTable(section);
    expect(screen.queryByTestId("status-host-owned")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-awaiting_input").className).toContain("status-attention-tint");
  });
});

describe("RepoWorkspaceTable — project roll-up respects host ownership", () => {
  it("does not flag needs_you at the project level when its only parked workspace is host-owned", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p-host" }),
          workspaces: [
            ws({
              task_id: "w-host",
              status: "awaiting_input",
              project_id: "p-host",
              unblock: unblockRecord({ outcome: "not_yet" }),
            }),
          ],
        },
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("aggregate-chip-project-p-host")).not.toHaveTextContent("needs you");
  });

  it("still flags needs_you at the project level when the host has reported it needs a human", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p-needs-human" }),
          workspaces: [
            ws({
              task_id: "w-needs-human",
              status: "awaiting_input",
              project_id: "p-needs-human",
              unblock: unblockRecord({ outcome: "needs_human" }),
            }),
          ],
        },
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("aggregate-chip-project-p-needs-human")).toHaveTextContent(
      "needs you",
    );
  });
});

describe("RepoWorkspaceTable — note truncation", () => {
  it("truncates a note to a single line, keeping the full first line available via the native title tooltip", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: "w-note-clamp", note: "a note that stays single-line" })],
    };
    renderTable(section);
    const note = screen.getByTestId("ws-note-w-note-clamp");
    expect(note.className).toContain("line-clamp-1");
    expect(note.className).not.toContain("line-clamp-3");
    expect(note).toHaveAttribute("title", "a note that stays single-line");
  });
});

describe("RepoWorkspaceTable — per-row action", () => {
  it("renders exactly one action control per feature row, never zero and never two", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: "action-1", status: "active" })],
    };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-action-1");
    expect(within(row).getAllByRole("button")).toHaveLength(1);
  });

  it("renders no checkbox and no bulk action bar anywhere in the table", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "no-bulk" })] };
    renderTable(section);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bulk-action-bar")).not.toBeInTheDocument();
  });

  it.each([
    ["active", "Stop", `/api/workspaces/status-active/stop`],
    ["completed", "Archive", `/api/workspaces/status-completed/archive`],
    ["paused", "Archive", `/api/workspaces/status-paused/archive`],
    ["archived", "Unarchive", `/api/workspaces/status-archived/unarchive`],
  ] as const)("%s status shows %s and hits its own route", async (status, label, route) => {
    let hit = false;
    server.use(
      http.post(route, () => {
        hit = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const taskId = `status-${status}`;
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: taskId, status })],
    };
    const reload = vi.fn();
    renderTable(section, { reload });
    const button = screen.getByTestId(`workspace-row-action-${taskId}`);
    expect(button).toHaveAccessibleName(`${label} — ${taskId}`);
    const user = userEvent.setup();
    await user.click(button);
    await waitFor(() => expect(hit).toBe(true));
    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("gives two same-status rows distinct accessible names for their action", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: "dup-a", status: "active" }), ws({ task_id: "dup-b", status: "active" })],
    };
    renderTable(section);
    expect(screen.getByTestId("workspace-row-action-dup-a")).toHaveAccessibleName("Stop — dup-a");
    expect(screen.getByTestId("workspace-row-action-dup-b")).toHaveAccessibleName("Stop — dup-b");
  });

  it("Stop fires immediately with no confirmation dialog", async () => {
    server.use(http.post("/api/workspaces/stop-now/stop", () => new HttpResponse(null, { status: 204 })));
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "stop-now", status: "active" })] };
    renderTable(section);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("workspace-row-action-stop-now"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reveals the action's description on hover and on focus", async () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "described-1", status: "active" })] };
    renderTable(section);
    const button = screen.getByTestId("workspace-row-action-described-1");
    const describedBy = button.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    const description = document.getElementById(describedBy ?? "");
    expect(description).toHaveClass("sr-only");
    const user = userEvent.setup();
    await user.hover(button);
    expect(description).not.toHaveClass("sr-only");
  });

  it("carries the blue action colour and a tinted hover area, matching the rail delete-bin's icon-button treatment", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "weight-1", status: "active" })] };
    renderTable(section);
    const button = screen.getByTestId("workspace-row-action-weight-1");
    expect(button.className).toContain("text-btn-action");
    expect(button.className).toContain("hover:bg-btn-action/15");
    expect(button.className).not.toContain("bg-btn-action text-surface");
    expect(button).toHaveAttribute("data-icon-only", "true");
  });
});

describe("RepoWorkspaceTable — showProjects off", () => {
  it("shows the plain Feature header (no literal 'Project')", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w1" })] };
    renderTable(section, { showProjects: false });
    const headerRow = screen.getByRole("table").querySelector("thead tr");
    expect(headerRow).toHaveTextContent("Feature");
    expect(headerRow).not.toHaveTextContent("Project · Feature");
  });

  it("renders every project-repo workspace as a flat row, with no project row and no Unassigned grouping header", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, unassigned: [ws({ task_id: "w1", project_id: "p1" }), ws({ task_id: "w2", project_id: null })], projectGroups: [] };
    renderTable(section, { showProjects: false });
    expect(screen.getByTestId("home-row-feature-w1")).toBeInTheDocument();
    expect(screen.getByTestId("home-row-feature-w2")).toBeInTheDocument();
    expect(screen.queryByTestId(/home-row-project-/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("home-row-unassigned-acme/sprocket")).not.toBeInTheDocument();
  });

  it("keeps the empty message on an empty section too (no crash)", () => {
    renderTable(EMPTY_SECTION, { showProjects: false });
    expect(screen.getByTestId("repo-empty-acme/sprocket")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("RepoWorkspaceTable — unbound chip", () => {
  it("renders the Unbound chip, using the ratified badge recipe, for a project with recorded intent but no binding", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [{ project: project({ id: "p-unbound", engine_key: null, intended_engine_key: "acme/sprocket" }), workspaces: [] }],
    };
    renderTable(section);
    const chip = screen.getByTestId("unbound-chip-p-unbound");
    expect(chip).toHaveTextContent("Unbound");
    expect(chip.className).toContain("inline-block rounded px-2 py-0.5 text-xs");
    expect(chip.className).toContain("bg-status-attention-tint/15");
    expect(chip.className).toContain("text-status-attention-fg");
  });

  it("does not render the chip for a bound project", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, projectGroups: [{ project: project({ id: "p-bound", engine_key: "acme/sprocket" }), workspaces: [] }] };
    renderTable(section);
    expect(screen.queryByTestId("unbound-chip-p-bound")).not.toBeInTheDocument();
  });

  it("does not render the chip for an unbound project with no recorded intent", () => {
    const section: RepoSectionModel = { ...EMPTY_SECTION, projectGroups: [{ project: project({ id: "p-noprov", engine_key: null, intended_engine_key: null }), workspaces: [] }] };
    renderTable(section);
    expect(screen.queryByTestId("unbound-chip-p-noprov")).not.toBeInTheDocument();
  });
});

describe("RepoWorkspaceTable — feature primary cell stacking", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stacks the task id, note, and badge row top to bottom in that order", () => {
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [ws({ task_id: "stack-1", note: "a note", team: "dependency_sweep", auto_archive_days: 14, updated_at: "2026-07-01T12:00:00Z" })],
    };
    renderTable(section);
    const row = screen.getByTestId("home-row-feature-stack-1");
    const link = within(row).getByRole("link");
    const note = screen.getByTestId("ws-note-stack-1");
    const badgeRow = row.querySelector("span.flex.min-w-0.flex-wrap.items-center.gap-2");
    expect(badgeRow).not.toBeNull();
    expect(link.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(note.compareDocumentPosition(badgeRow as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe("RepoWorkspaceTable — live silence-boundary re-render", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops showing the host-owned state and badge once the silence threshold elapses, with no prop change", () => {
    const now = Date.now();
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      unassigned: [
        ws({
          task_id: "row-ticking",
          status: "awaiting_input",
          unblock: unblockRecord({ outcome: "not_yet", at: now / 1000 }),
        }),
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("status-host-owned")).toBeInTheDocument();
    expect(screen.getByTestId("unblock-owner-badge-row-ticking")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(91 * 60 * 1000);
    });
    expect(screen.queryByTestId("status-host-owned")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-awaiting_input")).toBeInTheDocument();
    expect(screen.queryByTestId("unblock-owner-badge-row-ticking")).not.toBeInTheDocument();
  });

  it("stops flagging needs_you at the project level once a host-owned park's silence threshold elapses", () => {
    const now = Date.now();
    const section: RepoSectionModel = {
      ...EMPTY_SECTION,
      projectGroups: [
        {
          project: project({ id: "p-ticking" }),
          workspaces: [
            ws({
              task_id: "w-ticking",
              status: "awaiting_input",
              project_id: "p-ticking",
              unblock: unblockRecord({ outcome: "not_yet", at: now / 1000 }),
            }),
          ],
        },
      ],
    };
    renderTable(section);
    expect(screen.getByTestId("aggregate-chip-project-p-ticking")).not.toHaveTextContent("needs you");
    act(() => {
      vi.advanceTimersByTime(91 * 60 * 1000);
    });
    expect(screen.getByTestId("aggregate-chip-project-p-ticking")).toHaveTextContent("needs you");
  });
});

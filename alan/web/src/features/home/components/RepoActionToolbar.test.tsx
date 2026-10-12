import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { server } from "../../../test/server";
import type { Project } from "../../../api";
import RepoActionToolbar from "./RepoActionToolbar";
import type { RepoSectionModel } from "../../../lib/fold";
import type { BindOutcome, UseRepoActionsResult } from "../hooks/useRepoActions";

const BASE_SECTION: RepoSectionModel = {
  key: "acme/widget",
  title: "Widget",
  posture: "autonomous",
  projectGroups: [],
  unassigned: [],
  shippedCount: 0,
  archivedCount: 0,
};

function buildActions(overrides: Partial<UseRepoActionsResult> = {}): UseRepoActionsResult {
  return {
    modalOpen: false,
    openModal: vi.fn(),
    closeModal: vi.fn(),
    binding: false,
    bindOutcome: null,
    onProjectCreated: vi.fn(),
    maintenanceOutcome: null,
    maintenanceDispatching: false,
    maintenanceStopping: false,
    startMaintenance: vi.fn(),
    stopMaintenance: vi.fn(),
    hiding: false,
    hideRepo: vi.fn(),
    ...overrides,
  };
}

function renderToolbar(
  section: RepoSectionModel = BASE_SECTION,
  showProjects = true,
  actions: UseRepoActionsResult = buildActions(),
) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={<RepoActionToolbar section={section} showProjects={showProjects} actions={actions} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function projectResponse(id: string, title: string): Project {
  return {
    id,
    title,
    status: "active",
    doc_ref: null,
    autonomy: "propose",
    budget_usd: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  };
}

describe("RepoActionToolbar", () => {
  it("renders Start a project as a ghost button", () => {
    renderToolbar();
    const startProject = screen.getByTestId("repo-start-project-acme/widget");
    expect(startProject.getAttribute("data-variant")).toBe("ghost");
  });

  it("calls the actions' openModal when Start a project is clicked", async () => {
    const actions = buildActions();
    renderToolbar(BASE_SECTION, true, actions);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("repo-start-project-acme/widget"));
    expect(actions.openModal).toHaveBeenCalledTimes(1);
  });

  it("renders the New project modal when actions.modalOpen is true", () => {
    renderToolbar(BASE_SECTION, true, buildActions({ modalOpen: true }));
    expect(screen.getByTestId("new-project-dialog")).toBeInTheDocument();
  });

  it("does not render the Start a project action when showProjects is off", () => {
    renderToolbar(BASE_SECTION, false);
    expect(screen.queryByTestId("repo-start-project-acme/widget")).not.toBeInTheDocument();
  });

  it("renders a bound-project bind notice with a link to the project", () => {
    const bindOutcome: BindOutcome = { kind: "bound", project: projectResponse("proj-1", "New Thing") };
    renderToolbar(BASE_SECTION, true, buildActions({ bindOutcome }));
    const notice = screen.getByTestId("repo-bind-notice-acme/widget");
    expect(notice).toHaveTextContent("bound to this repository");
    expect(within(notice).getByRole("link", { name: "New Thing" })).toHaveAttribute(
      "href",
      "/projects/proj-1",
    );
  });

  it("surfaces an honest typed notice for each unbound rejection reason", () => {
    const bindOutcome: BindOutcome = {
      kind: "unbound",
      project: projectResponse("proj-2", "Fresh Repo Project"),
      reason: "preflight",
      detail: "engine preflight not passing",
    };
    renderToolbar(BASE_SECTION, true, buildActions({ bindOutcome }));
    const notice = screen.getByTestId("repo-bind-notice-acme/widget");
    expect(notice).toHaveTextContent("not yet bound to this repository");
    expect(notice).toHaveTextContent("preflight");
  });

  it("surfaces a typed notice for a live-workspaces rejection reason", () => {
    const bindOutcome: BindOutcome = {
      kind: "unbound",
      project: projectResponse("proj-3", "Live Workspaces Project"),
      reason: "live_workspaces",
      detail: "project has live workspaces",
    };
    renderToolbar(BASE_SECTION, true, buildActions({ bindOutcome }));
    expect(screen.getByTestId("repo-bind-notice-acme/widget")).toHaveTextContent(
      "live workspaces blocking a rebind",
    );
  });

  it("surfaces a typed notice for an unknown-engine rejection reason", () => {
    const bindOutcome: BindOutcome = {
      kind: "unbound",
      project: projectResponse("proj-4", "Unknown Engine Project"),
      reason: "unknown_engine",
      detail: "unknown engine: 'acme/widget'",
    };
    renderToolbar(BASE_SECTION, true, buildActions({ bindOutcome }));
    expect(screen.getByTestId("repo-bind-notice-acme/widget")).toHaveTextContent(
      "engine could not be found",
    );
  });

  it("surfaces the raw detail text for an 'other' rejection reason", () => {
    const bindOutcome: BindOutcome = {
      kind: "unbound",
      project: projectResponse("proj-5", "Other Failure Project"),
      reason: "other",
      detail: "service unavailable",
    };
    renderToolbar(BASE_SECTION, true, buildActions({ bindOutcome }));
    expect(screen.getByTestId("repo-bind-notice-acme/widget")).toHaveTextContent("service unavailable");
  });

  it("does not render a bind notice when showProjects is off, even with a recorded outcome", () => {
    const bindOutcome: BindOutcome = { kind: "bound", project: projectResponse("proj-1", "New Thing") };
    renderToolbar(BASE_SECTION, false, buildActions({ bindOutcome }));
    expect(screen.queryByTestId("repo-bind-notice-acme/widget")).not.toBeInTheDocument();
  });

  it("calls onProjectCreated when the New project modal submits a project", async () => {
    server.use(
      http.post("/api/projects", () => HttpResponse.json(projectResponse("proj-1", "New Thing"), { status: 201 })),
    );
    const actions = buildActions({ modalOpen: true });
    renderToolbar(BASE_SECTION, true, actions);
    const user = userEvent.setup();
    await user.type(screen.getByTestId("new-project-title"), "New Thing");
    await user.click(screen.getByTestId("new-project-submit"));
    await waitFor(() => expect(actions.onProjectCreated).toHaveBeenCalledTimes(1));
  });
});

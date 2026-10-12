import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import Project from "./Project";

function renderAt(initial = "/projects/alpha") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
        <Route path="/projects" element={<div data-testid="projects-list-page">projects</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const BASE_PROJECT = {
  id: "alpha",
  title: "Alpha",
  status: "active",
  doc_ref: null,
  autonomy: "propose",
  budget_usd: null,
  engine_key: null,
  created_at: "2026-05-10T08:00:00Z",
  updated_at: "2026-05-11T08:00:00Z",
  workspaces: [],
  ready_to_complete: true,
  roadmap: [],
  budget: null,
};

const EMPTY_DOC = {
  vision: "some vision",
  scope: "some scope",
  decision_log: "",
  discovered_facts: "",
};

function mockProject(data: Partial<typeof BASE_PROJECT> = {}) {
  server.use(
    http.get("/api/projects/alpha", () => HttpResponse.json({ ...BASE_PROJECT, ...data })),
  );
}

function mockDoc(data: Partial<typeof EMPTY_DOC> = {}) {
  server.use(
    http.get("/api/projects/alpha/doc", () => HttpResponse.json({ ...EMPTY_DOC, ...data })),
  );
}

function gatedRejection(status: number, detail: string) {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const handler = async () => {
    await gate;
    return HttpResponse.json({ detail }, { status });
  };
  return { handler, release: () => release() };
}

describe("Project busy-state sites onto the shared action hook", () => {
  it("AutonomyBudgetEditor's Save button carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject();
    mockDoc();
    const { handler, release } = gatedRejection(422, "invalid autonomy");
    server.use(http.patch("/api/projects/alpha", handler));
    const user = userEvent.setup();
    renderAt();
    const saveButton = await screen.findByTestId("project-autonomy-save");
    await user.click(saveButton);
    await waitFor(() => expect(saveButton).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("project-autonomy-error");
    expect(error).toHaveTextContent("invalid autonomy");
    expect(saveButton).not.toHaveAttribute("disabled");
  });

  it("AutonomyBudgetEditor's Save button clears busy after a successful settle", async () => {
    mockProject();
    mockDoc();
    server.use(http.patch("/api/projects/alpha", () => HttpResponse.json(BASE_PROJECT)));
    const user = userEvent.setup();
    renderAt();
    const saveButton = await screen.findByTestId("project-autonomy-save");
    await user.click(saveButton);
    await screen.findByTestId("project-autonomy-saved");
    expect(saveButton).not.toHaveAttribute("disabled");
  });

  it("ArchiveToggleButton carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject();
    mockDoc();
    const { handler, release } = gatedRejection(409, "project has workspaces");
    server.use(http.patch("/api/projects/alpha", handler));
    const user = userEvent.setup();
    renderAt();
    const toggle = await screen.findByTestId("project-archive-toggle");
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("project-archive-error");
    expect(error).toHaveTextContent("project has workspaces");
    expect(toggle).not.toHaveAttribute("disabled");
  });

  it("ArchiveToggleButton clears busy and flips its label after a successful settle", async () => {
    mockProject();
    mockDoc();
    server.use(
      http.patch("/api/projects/alpha", () =>
        HttpResponse.json({ ...BASE_PROJECT, status: "archived" }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    const toggle = await screen.findByTestId("project-archive-toggle");
    await user.click(toggle);
    await waitFor(() => expect(toggle).toHaveTextContent("Unarchive"));
    expect(toggle).not.toHaveAttribute("disabled");
  });

  it("DeleteProjectDialog's confirm button carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject();
    mockDoc();
    const { handler, release } = gatedRejection(409, "project_has_workspaces");
    server.use(http.delete("/api/projects/alpha", handler));
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("project-delete-button"));
    const confirmButton = await screen.findByTestId("delete-project-confirm");
    await user.click(confirmButton);
    await waitFor(() => expect(confirmButton).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("delete-project-error");
    expect(error).toHaveTextContent("project_has_workspaces");
    expect(confirmButton).not.toHaveAttribute("disabled");
  });

  it("DeleteProjectDialog navigates away on a successful settle", async () => {
    mockProject();
    mockDoc();
    server.use(
      http.delete("/api/projects/alpha", () => new HttpResponse(null, { status: 204 })),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("project-delete-button"));
    await user.click(await screen.findByTestId("delete-project-confirm"));
    expect(await screen.findByTestId("projects-list-page")).toBeInTheDocument();
  });

  it("ProposeAmendmentModal's submit button carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject();
    mockDoc();
    const { handler, release } = gatedRejection(500, "amendment failed");
    server.use(http.post("/api/projects/alpha/amendments", handler));
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("propose-amendment-button"));
    await user.type(screen.getByTestId("amendment-decision-entry"), "shipping this amendment");
    const submitButton = screen.getByTestId("propose-amendment-submit");
    await user.click(submitButton);
    await waitFor(() => expect(submitButton).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("propose-amendment-error");
    expect(error).toHaveTextContent("amendment failed");
    expect(submitButton).not.toHaveAttribute("disabled");
  });

  it("ProposeAmendmentModal closes on a successful settle", async () => {
    mockProject();
    mockDoc();
    server.use(
      http.post("/api/projects/alpha/amendments", () => HttpResponse.json({}, { status: 201 })),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("propose-amendment-button"));
    await user.type(screen.getByTestId("amendment-decision-entry"), "shipping this amendment");
    await user.click(screen.getByTestId("propose-amendment-submit"));
    expect(await screen.findByTestId("propose-amendment-success")).toBeInTheDocument();
    expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
  });

  it("ReadyToCompleteButton carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject({ ready_to_complete: true });
    mockDoc();
    const { handler, release } = gatedRejection(422, "not ready");
    server.use(http.post("/api/projects/alpha/complete", handler));
    const user = userEvent.setup();
    renderAt();
    const completeButton = await screen.findByTestId("project-complete-button");
    await user.click(completeButton);
    await waitFor(() => expect(completeButton).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("project-complete-error");
    expect(error).toHaveTextContent("not ready");
    expect(completeButton).not.toHaveAttribute("disabled");
  });

  it("ReadyToCompleteButton clears busy after a successful settle, shipping the project", async () => {
    mockProject({ ready_to_complete: true });
    mockDoc();
    server.use(
      http.post("/api/projects/alpha/complete", () =>
        HttpResponse.json({ ...BASE_PROJECT, status: "completed" }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("project-complete-button"));
    expect(await screen.findByTestId("project-complete-shipped")).toBeInTheDocument();
  });

  it("ForceCompleteDisclosure's submit button carries a real disabled attribute while pending, and loses it after a failed settle", async () => {
    mockProject({ ready_to_complete: false });
    mockDoc();
    const { handler, release } = gatedRejection(422, "still not ready");
    server.use(http.post("/api/projects/alpha/complete", handler));
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("project-force-complete-disclosure");
    await user.type(screen.getByTestId("project-force-reason"), "shipping anyway");
    const forceButton = screen.getByTestId("project-force-complete-submit");
    await user.click(forceButton);
    await waitFor(() => expect(forceButton).toHaveAttribute("disabled"));

    release();
    const error = await screen.findByTestId("project-force-complete-error");
    expect(error).toHaveTextContent("still not ready");
    expect(forceButton).not.toHaveAttribute("disabled");
  });

  it("ForceCompleteDisclosure clears busy after a successful settle, shipping the project", async () => {
    mockProject({ ready_to_complete: false });
    mockDoc();
    server.use(
      http.post("/api/projects/alpha/complete", () =>
        HttpResponse.json({ ...BASE_PROJECT, status: "completed" }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    await screen.findByTestId("project-force-complete-disclosure");
    await user.type(screen.getByTestId("project-force-reason"), "shipping anyway");
    await user.click(screen.getByTestId("project-force-complete-submit"));
    expect(await screen.findByTestId("project-complete-shipped")).toBeInTheDocument();
  });
});

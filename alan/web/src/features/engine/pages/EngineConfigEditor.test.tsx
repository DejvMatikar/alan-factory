import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import EngineConfigEditor from "./EngineConfigEditor";

function renderAt(projectId = "alpha") {
  return render(
    <MemoryRouter initialEntries={[`/projects/${projectId}/engine`]}>
      <Routes>
        <Route path="/projects/:projectId/engine" element={<EngineConfigEditor />} />
        <Route
          path="/repos/:owner/:repo/config"
          element={<div data-testid="repo-engine-config-target">repo engine config</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function renderWithoutProjectId() {
  return render(
    <MemoryRouter initialEntries={["/engine-no-project"]}>
      <Routes>
        <Route path="/engine-no-project" element={<EngineConfigEditor />} />
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
  engine_key: "acme/widget" as string | null,
  created_at: "2026-05-10T08:00:00Z",
  updated_at: "2026-05-11T08:00:00Z",
  workspaces: [],
  ready_to_complete: true,
};

function mockProject(overrides: Partial<typeof BASE_PROJECT> = {}) {
  server.use(
    http.get("/api/projects/alpha", () => HttpResponse.json({ ...BASE_PROJECT, ...overrides })),
  );
}

describe("EngineConfigEditor — resolver", () => {
  it("shows a loading state before the project resolves", () => {
    mockProject();
    renderAt();
    expect(screen.getByTestId("engine-config-editor-loading")).toBeInTheDocument();
  });

  it("shows an error state when the project fetch fails", async () => {
    server.use(
      http.get("/api/projects/alpha", () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    renderAt();
    expect(await screen.findByTestId("engine-config-editor-error")).toHaveTextContent("boom");
  });

  it("stays in a loading state and never crashes when there is no project id in the route", () => {
    renderWithoutProjectId();
    expect(screen.getByTestId("engine-config-editor-loading")).toBeInTheDocument();
  });

  it("does not update state after unmount when the project fetch errors", async () => {
    const gate: { settle: ((response: Response) => void) | null } = { settle: null };
    server.use(
      http.get(
        "/api/projects/alpha",
        () => new Promise<Response>((resolve) => { gate.settle = resolve; }),
      ),
    );
    const { unmount } = renderAt();
    await waitFor(() => {
      expect(gate.settle).not.toBeNull();
    });
    unmount();
    gate.settle!(HttpResponse.json({ detail: "boom" }, { status: 500 }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  it("shows an explicit error, not a blank redirect, when the project has no engine_key", async () => {
    mockProject({ engine_key: null });
    renderAt();
    expect(await screen.findByTestId("engine-config-editor-error")).toHaveTextContent(
      /isn't bound to an engine/,
    );
  });

  it("redirects to the repository-scoped config route for the project's bound engine", async () => {
    mockProject();
    renderAt();
    expect(await screen.findByTestId("repo-engine-config-target")).toBeInTheDocument();
  });

  it("shows an explicit error, not a corrupted redirect, when the project's engine_key has no owner/repo separator", async () => {
    mockProject({ engine_key: "malformed" });
    renderAt();
    expect(await screen.findByTestId("engine-config-editor-error")).toHaveTextContent(
      /engine reference is malformed/,
    );
  });

  it("redirects to the repository-scoped config route for a project bound to the default engine", async () => {
    mockProject({ engine_key: "DejvMatikar/alan" });
    render(
      <MemoryRouter initialEntries={["/projects/alpha/engine"]}>
        <Routes>
          <Route path="/projects/:projectId/engine" element={<EngineConfigEditor />} />
          <Route
            path="/repos/DejvMatikar/alan/config"
            element={<div data-testid="repo-engine-config-target">repo engine config</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByTestId("repo-engine-config-target")).toBeInTheDocument();
  });
});

import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";

import { server } from "./server";
import Project from "../features/project/pages/Project";

export function renderAt(initial = "/projects/alpha") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
        <Route path="/projects" element={<div data-testid="projects-list-page">projects</div>} />
        <Route
          path="/workspaces/:taskId"
          element={<div data-testid="ws-detail-page">workspace</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function NavigateButton({ to }: Readonly<{ to: string }>) {
  const navigate = useNavigate();
  const handleClick = (): void => {
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    navigate(to);
  };
  return (
    <button data-testid="nav-to-other-project" onClick={handleClick}>
      go
    </button>
  );
}

export function renderWithNav(from: string, to: string) {
  return render(
    <MemoryRouter initialEntries={[from]}>
      <NavigateButton to={to} />
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
      </Routes>
    </MemoryRouter>,
  );
}

export function renderAtWithState(pathname: string, state: unknown) {
  return render(
    <MemoryRouter initialEntries={[{ pathname, state }]}>
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
      </Routes>
    </MemoryRouter>,
  );
}

interface BudgetView {
  state: "normal" | "approaching" | "paused";
  burn_usd: number;
  budget_usd: number | null;
  remaining_usd: number | null;
}

export const BASE_PROJECT: {
  id: string;
  title: string;
  status: string;
  doc_ref: string | null;
  autonomy: string;
  budget_usd: number | null;
  engine_key: string | null;
  created_at: string;
  updated_at: string;
  workspaces: unknown[];
  ready_to_complete: boolean;
  roadmap: unknown[];
  budget: BudgetView | null;
} = {
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

export const EMPTY_DOC = {
  vision: "",
  scope: "",
  decision_log: "",
  discovered_facts: "",
};

export function mockProject(data: Partial<typeof BASE_PROJECT> = {}) {
  server.use(
    http.get("/api/projects/alpha", () => HttpResponse.json({ ...BASE_PROJECT, ...data })),
  );
}

export function mockDoc(data: Partial<typeof EMPTY_DOC> = {}) {
  server.use(
    http.get("/api/projects/alpha/doc", () => HttpResponse.json({ ...EMPTY_DOC, ...data })),
  );
}

export function trackProjectPatch(): () => boolean {
  let called = false;
  server.use(
    http.patch("/api/projects/alpha", () => {
      called = true;
      return HttpResponse.json(BASE_PROJECT);
    }),
  );
  return () => called;
}

export function trackProjectDelete(): () => boolean {
  let called = false;
  server.use(
    http.delete("/api/projects/alpha", () => {
      called = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  return () => called;
}

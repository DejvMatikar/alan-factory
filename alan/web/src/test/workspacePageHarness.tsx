import { render } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach } from "vitest";

import { server } from "./server";
import { makeWorkspaceDetail } from "./workspaceFixtures";
import Workspace from "../features/workspace/pages/Workspace";

export const BASE = makeWorkspaceDetail({ title: "T1 Title", description: "do it" });

export function renderAt(taskId = "t1") {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${taskId}`]}>
      <Routes>
        <Route path="/workspaces/:taskId" element={<Workspace />} />
        <Route path="/" element={<div data-testid="list-page">list</div>} />
        <Route
          path="/repos/:owner/:repo"
          element={<div data-testid="repo-dashboard-page">repo dashboard</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

export function noEventsHandler() {
  return http.get("/api/workspaces/t1/events", () => HttpResponse.error());
}

export function installWorkspaceDefaultHandlers(): void {
  beforeEach(() => {
    server.use(
      http.get(/\/api\/workspaces\/[^/]+\/dispatch-instructions$/, () => HttpResponse.json({})),
      http.get(/\/api\/workspaces\/[^/]+\/sticky-instructions$/, () =>
        HttpResponse.json({ text: null, origin: null }),
      ),
    );
  });
}

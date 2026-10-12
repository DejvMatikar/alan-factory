import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import Project from "./Project";

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
  roadmap: [
    {
      id: "item-1",
      title: "Item 1",
      text: "",
      order: 0,
      status: "not_started",
      merged_prs: [],
    },
  ],
  budget: null,
};

const EMPTY_DOC = {
  vision: "",
  scope: "",
  decision_log: "",
  discovered_facts: "",
};

function renderAt() {
  return render(
    <MemoryRouter initialEntries={["/projects/alpha"]}>
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockProject(status: string) {
  server.use(
    http.get("/api/projects/alpha", () =>
      HttpResponse.json({ ...BASE_PROJECT, status }),
    ),
    http.get("/api/projects/alpha/doc", () => HttpResponse.json(EMPTY_DOC)),
  );
}

function trackNextItemProposalFetch(): () => boolean {
  let called = false;
  server.use(
    http.get("/api/projects/alpha/roadmap/next-item-proposal", () => {
      called = true;
      return HttpResponse.json({ proposal: null, budget_state: "normal" });
    }),
  );
  return () => called;
}

describe("Project next-item proposal gate", () => {
  it("does not fetch the next-item proposal for an archived project", async () => {
    mockProject("archived");
    const wasCalled = trackNextItemProposalFetch();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("next-item-proposal-loading")).not.toBeInTheDocument();
    expect(wasCalled()).toBe(false);
  });

  it("does not fetch the next-item proposal for a completed project", async () => {
    mockProject("completed");
    const wasCalled = trackNextItemProposalFetch();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    expect(screen.queryByTestId("next-item-proposal-loading")).not.toBeInTheDocument();
    expect(wasCalled()).toBe(false);
  });

  it("fetches the next-item proposal for an active project", async () => {
    mockProject("active");
    const wasCalled = trackNextItemProposalFetch();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Alpha" });
    await waitFor(() => {
      expect(wasCalled()).toBe(true);
    });
  });
});

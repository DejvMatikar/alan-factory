import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import Project from "./Project";

const MD_LG_TAIL =
  " rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80";

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
  vision: "",
  scope: "",
  decision_log: "",
  discovered_facts: "",
};

function renderProject() {
  server.use(
    http.get("/api/projects/alpha", () => HttpResponse.json(BASE_PROJECT)),
    http.get("/api/projects/alpha/doc", () => HttpResponse.json(EMPTY_DOC)),
  );
  return render(
    <MemoryRouter initialEntries={["/projects/alpha"]}>
      <Routes>
        <Route path="/projects/:projectId" element={<Project />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Project dialogs adopt the shared DialogFrame", () => {
  it("renders the delete-project dialog through the byte-identical shared frame class", async () => {
    renderProject();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("project-delete-button"));
    expect(screen.getByTestId("delete-project-dialog").className).toBe(
      "fixed inset-0 m-auto w-[90vw] max-w-lg" + MD_LG_TAIL,
    );
  });

  it("gives the delete-project dialog an accessible name matching its heading", async () => {
    renderProject();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("project-delete-button"));
    expect(screen.getByRole("dialog", { name: "Delete project" })).toBeInTheDocument();
  });

  it("restores focus to the launching button once the delete-project dialog unmounts", async () => {
    renderProject();
    const user = userEvent.setup();
    const opener = await screen.findByTestId("project-delete-button");
    await user.click(opener);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("delete-project-dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("renders the propose-amendment dialog through the byte-identical shared frame class", async () => {
    renderProject();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("propose-amendment-button"));
    expect(screen.getByTestId("propose-amendment-dialog").className).toBe(
      "fixed inset-0 m-auto w-[90vw] max-w-lg" + MD_LG_TAIL,
    );
  });

  it("gives the propose-amendment dialog an accessible name matching its purpose", async () => {
    renderProject();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("propose-amendment-button"));
    expect(
      screen.getByRole("dialog", { name: "Propose vision/scope amendment" }),
    ).toBeInTheDocument();
  });

  it("restores focus to the launching button once the propose-amendment dialog unmounts", async () => {
    renderProject();
    const user = userEvent.setup();
    const opener = await screen.findByTestId("propose-amendment-button");
    await user.click(opener);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("propose-amendment-dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

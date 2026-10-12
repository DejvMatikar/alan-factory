import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import Workspace from "./Workspace";

const BASE = {
  task_id: "t1",
  title: "T1 Title",
  description: "do it",
  status: "active" as const,
  revisit_counts: {},
  auto_dispatch: false,
  created_at: "2026-05-12T08:00:00Z",
  updated_at: "2026-05-12T08:00:00Z",
  history: [],
  latest_handover: null,
  has_running_job: false,
  has_resumable_failed_job: false,
  requeue_count: 0,
  tasks: [],
  scope_audit: [],
  requirements: null,
  total_cost_usd: null,
};

function renderAt() {
  return render(
    <MemoryRouter initialEntries={["/workspaces/t1"]}>
      <Routes>
        <Route path="/workspaces/:taskId" element={<Workspace />} />
        <Route path="/" element={<div data-testid="list-page">list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function noEventsHandler() {
  return http.get("/api/workspaces/t1/events", () => HttpResponse.error());
}

beforeEach(() => {
  server.use(
    http.get(/\/api\/workspaces\/[^/]+\/dispatch-instructions$/, () => HttpResponse.json({})),
    http.get(/\/api\/workspaces\/[^/]+\/sticky-instructions$/, () =>
      HttpResponse.json({ text: null, origin: null }),
    ),
    http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
    http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
    noEventsHandler(),
  );
});

describe("Workspace delete dialog adopts the shared DialogFrame", () => {
  it("renders with the shared dialog-frame class", async () => {
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByTestId("delete-dialog").className).toBe(
      "fixed inset-0 m-auto w-[90vw] max-w-md rounded-lg border border-border bg-surface-elevated p-0 text-text-primary shadow-lg backdrop:bg-surface/80",
    );
  });

  it("keeps its accessible name", async () => {
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByRole("dialog", { name: "Delete workspace?" })).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    renderAt();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    const dialog = screen.getByTestId("delete-dialog");
    const form = dialog.querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByTestId("delete-dialog")).not.toBeInTheDocument();
    });
  });

  it("restores focus to the launching Delete button once the dialog unmounts", async () => {
    renderAt();
    const opener = await screen.findByRole("button", { name: "Delete" });
    await userEvent.click(opener);
    await screen.findByTestId("delete-dialog");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByTestId("delete-dialog")).not.toBeInTheDocument();
    });
    expect(opener).toHaveFocus();
  });
});

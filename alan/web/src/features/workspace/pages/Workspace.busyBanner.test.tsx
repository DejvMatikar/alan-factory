import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import { makeHandover, makeWorkspaceDetail } from "../../../test/workspaceFixtures";
import Workspace from "./Workspace";

const CORE_ERROR_BANNER = "rounded-md bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg";
const PENDING_EXTERNAL_OPTION = "Complete the out-of-fleet step yourself.";

const BASE = makeWorkspaceDetail({ title: "T1 Title", description: "do it" });

function pendingExternalBase() {
  return makeWorkspaceDetail({
    status: "awaiting_input",
    latest_handover: makeHandover({
      open_questions: [
        {
          id: "q1",
          question: "Verifier declared this finding unresolvable.",
          proposed_answer_space: [PENDING_EXTERNAL_OPTION],
          resolver_role: "human",
          blocking: true,
          prior_attempts: [],
          question_class: "verification_escalation",
          recommended: PENDING_EXTERNAL_OPTION,
          answer: PENDING_EXTERNAL_OPTION,
          option_resolvers: { [PENDING_EXTERNAL_OPTION]: "verification" },
          option_effects: { [PENDING_EXTERNAL_OPTION]: "pending_external" },
        },
      ],
    }),
  });
}

function renderAt(taskId = "t1") {
  return render(
    <MemoryRouter initialEntries={[`/workspaces/${taskId}`]}>
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
    http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
  );
});

describe("Workspace busy-state sites onto the shared action hook", () => {
  it("keeps a real disabled attribute on the restart-check button while pending, and loses it once the dispatch settles", async () => {
    const gate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(pendingExternalBase())),
      noEventsHandler(),
      http.post("/api/workspaces/t1/tasks/t1/dispatch", async () => {
        await new Promise<void>((resolve) => {
          gate.resolve = resolve;
        });
        return HttpResponse.json({ job_id: 1, invocation_id: "inv_002" });
      }),
    );
    const user = userEvent.setup();
    renderAt();
    const button = await screen.findByTestId("decision-card-restart-check");
    expect(button).not.toBeDisabled();
    await user.click(button);
    await waitFor(() => expect(button).toHaveAttribute("disabled"));

    gate.resolve?.();
    await waitFor(() => expect(button).not.toHaveAttribute("disabled"));
  });

  it("keeps a real disabled attribute on the delete-workspace confirm button while pending, loses it after a failed settle, and renders the absorbed banner with no data-testid", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
      http.delete("/api/workspaces/t1", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    const confirmButton = await screen.findByRole("button", { name: /^Delete workspace/ });
    expect(confirmButton).not.toHaveAttribute("disabled");
    await user.click(confirmButton);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("workspace has active jobs");
    expect(alert.className).toBe(CORE_ERROR_BANNER);
    expect(alert).not.toHaveAttribute("data-testid");
    expect(confirmButton).not.toHaveAttribute("disabled");
  });

  it("renders the archive-error banner with the absorbed class string and its data-testid", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () =>
        HttpResponse.json({ detail: "workspace has active jobs" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Archive" }));
    const alert = await screen.findByTestId("archive-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert.className).toBe(CORE_ERROR_BANNER);
  });

  it("renders the stop-resume-error banner with the absorbed class string and its data-testid", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json({ ...BASE, status: "paused" })),
      http.get("/api/workspaces/t1/invocations", () => HttpResponse.json([])),
      noEventsHandler(),
      http.post("/api/workspaces/t1/resume", () =>
        HttpResponse.json({ detail: "workspace is active; cannot resume" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTestId("resume-button"));
    const alert = await screen.findByTestId("stop-resume-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert.className).toBe(CORE_ERROR_BANNER);
  });
});

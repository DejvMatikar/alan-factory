import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { api } from "../../../api";
import { server } from "../../../test/server";
import Workspace from "./Workspace";

const VERIFICATION_OPTION = "Complete the out-of-fleet step yourself, then re-dispatch verification.";
const WAIVE_OPTION = "Waive this finding and proceed to merge.";

const BASE = {
  task_id: "t1",
  title: "T1 Title",
  description: "do it",
  status: "awaiting_input" as const,
  park_reason: "question" as const,
  revisit_counts: {},
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

function question(overrides: Record<string, unknown> = {}) {
  return {
    id: "q1",
    question: "Verifier declared this finding unresolvable: needs vault access.",
    proposed_answer_space: [VERIFICATION_OPTION, WAIVE_OPTION],
    resolver_role: "human",
    blocking: true,
    prior_attempts: [],
    question_class: "verification_escalation",
    recommended: VERIFICATION_OPTION,
    answer: null,
    option_resolvers: { [VERIFICATION_OPTION]: "verification", [WAIVE_OPTION]: "pr_merge" },
    option_effects: { [VERIFICATION_OPTION]: "pending_external", [WAIVE_OPTION]: "closes" },
    ...overrides,
  };
}

function handover(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1.22",
    handover_id: "h1",
    handover_type: "escalation",
    task_id: "t1",
    workspace_id: "t1",
    from_team: "verification",
    from_invocation_id: "inv_001",
    to_team_suggestions: [],
    iteration: 1,
    revisit_of: null,
    timestamp_utc: "2026-05-12T08:00:00Z",
    next_hop: null,
    decisions: [],
    open_questions: [question()],
    assumptions: [],
    constraints_inherited: [],
    artifacts_produced: [],
    full_synthesis: "needs a human answer",
    forbidden_actions: [],
    prior_attempts_summary: null,
    pr_number: null,
    files_to_edit: [],
    test_scenarios: [],
    ...overrides,
  };
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
  );
});

describe("pending-external answered decision card", () => {
  it("no longer exposes answerQuestion on the api client surface", () => {
    expect((api as Record<string, unknown>).answerQuestion).toBeUndefined();
  });

  it("renders the pending-external card together with the read-only question from wire state on a fresh mount", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          latest_handover: handover({
            open_questions: [question({ answer: VERIFICATION_OPTION })],
          }),
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("decision-card-pending-external")).toBeInTheDocument();
    expect(screen.getByTestId("decision-card-restart-check")).toBeInTheDocument();
    expect(screen.getByText(question().question)).toBeInTheDocument();
    expect(screen.getByTestId("open-question-answer")).toHaveTextContent(
      `Answer: ${VERIFICATION_OPTION}`,
    );
  });

  it("a closes-effect answer never renders the pending-external card but still shows the read-only question", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          latest_handover: handover({
            open_questions: [question({ answer: WAIVE_OPTION })],
          }),
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByTestId("decision-card");
    expect(screen.queryByTestId("decision-card-pending-external")).toBeNull();
    expect(screen.getByTestId("open-question-answer")).toHaveTextContent(`Answer: ${WAIVE_OPTION}`);
  });

  it("restart dispatches the option_resolvers-derived team with no instructions, and disables while in flight", async () => {
    let capturedBody: unknown = null;
    const dispatchGate: { resolve: (() => void) | null } = { resolve: null };
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          latest_handover: handover({
            open_questions: [question({ answer: VERIFICATION_OPTION })],
          }),
        }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/tasks/t1/dispatch", async ({ request }) => {
        capturedBody = await request.json();
        await new Promise<void>((resolve) => {
          dispatchGate.resolve = resolve;
        });
        return HttpResponse.json({ job_id: 1, invocation_id: "inv_002" });
      }),
    );
    const user = userEvent.setup();
    renderAt();
    const button = await screen.findByTestId("decision-card-restart-check");
    await user.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    expect(capturedBody).toEqual({ team: "verification" });

    dispatchGate.resolve?.();
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("falls back to the handover's from_team when option_resolvers has no mapping for the answer", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          latest_handover: handover({
            from_team: "pr_merge",
            open_questions: [
              question({ answer: VERIFICATION_OPTION, option_resolvers: {} }),
            ],
          }),
        }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/tasks/t1/dispatch", async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ job_id: 1, invocation_id: "inv_003" });
      }),
    );
    const user = userEvent.setup();
    renderAt();
    const button = await screen.findByTestId("decision-card-restart-check");
    await user.click(button);

    await waitFor(() => expect(capturedBody).toEqual({ team: "pr_merge" }));
  });

  it("shows an error message and re-enables the button when the restart dispatch fails", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          latest_handover: handover({
            open_questions: [question({ answer: VERIFICATION_OPTION })],
          }),
        }),
      ),
      noEventsHandler(),
      http.post("/api/workspaces/t1/tasks/t1/dispatch", () =>
        HttpResponse.json({ detail: "posture withheld" }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderAt();
    const button = await screen.findByTestId("decision-card-restart-check");
    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent("posture withheld");
    await waitFor(() => expect(button).not.toBeDisabled());
  });
});

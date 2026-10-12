import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import BuildChat from "./BuildChat";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const BUILD_CHAT_PATH = "/api/build-chat/sessions";

function sessionResp(overrides = {}) {
  return {
    session_id: SID,
    created_at: "2026-05-01T00:00:00Z",
    messages: [],
    in_flight_turn: null,
    brief: null,
    ...overrides,
  };
}

const COUNTER_CASE = {
  against: "the acceptance clauses are still thin",
  cheapest_80: "ship the read path only, defer writes",
  cost_estimate: "insufficient data",
};

function readyProposal(overrides: object = {}) {
  return {
    status: "ready",
    proposal: {
      team: "implementation_plan",
      rationale: "Build brief ready to dispatch to implementation planning.",
      instructions: null,
      scope_audit: [],
      title: "t",
      acceptance_clauses: [],
      counter_case: COUNTER_CASE,
      ...overrides,
    },
    collision: { state: "green", sentence: "No overlapping work found.", match: null },
  };
}

function renderBuildChat(initialPath = "/build") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/build" element={<BuildChat />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
        <Route path="/workspaces/:taskId" element={<div data-testid="workspace-page">ws</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function summarizeTo(content: string) {
  server.use(
    http.post(`${BUILD_CHAT_PATH}/${SID}/summarize`, () =>
      HttpResponse.json({ content, updated_at: "2026-05-01T01:00:00Z" }),
    ),
  );
  const user = userEvent.setup();
  await waitFor(() => expect(screen.getByTestId("summarize-button")).not.toBeDisabled());
  await user.click(screen.getByTestId("summarize-button"));
  await waitFor(() =>
    expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true"),
  );
  await waitFor(() => expect(screen.queryByTestId("summary-empty")).not.toBeInTheDocument(), {
    timeout: 3000,
  });
  return user;
}

describe("BuildChat — inline Summary-tab proposal panel", () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get(`${BUILD_CHAT_PATH}/${SID}`, () => HttpResponse.json(sessionResp())),
      http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
    );
  });

  describe("Staleness contract", () => {
    it("editing the brief after a proposal settles swaps confirm for repropose, dims the panel, and repropose dispatches the fresh (not stale) proposal", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal({ title: "stale-title", rationale: "stale rationale" })),
        ),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      );
      renderBuildChat();
      const user = await summarizeTo("original brief");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent("stale rationale");
      expect(screen.getByTestId("proposal-panel").className).not.toMatch(/border-border\/40/);

      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal({ title: "fresh-title", rationale: "fresh rationale" })),
        ),
      );
      await user.click(screen.getByTestId("summary-source-toggle"));
      const textarea = screen.getByTestId("summary-textarea");
      await user.clear(textarea);
      await user.type(textarea, "edited brief");
      await user.tab();
      await waitFor(() => expect(screen.getByTestId("summary-textarea")).toHaveValue("edited brief"));

      await waitFor(() => expect(screen.getByTestId("dispatch-proposal-repropose")).toBeInTheDocument());
      expect(screen.queryByTestId("dispatch-proposal-confirm")).not.toBeInTheDocument();
      expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
        "Brief changed since this proposal",
      );
      expect(screen.getByTestId("proposal-panel").className).toMatch(/border-border\/40/);
      expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent("stale rationale");

      await user.click(screen.getByTestId("dispatch-proposal-repropose"));
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.getByTestId("dispatch-proposal-rationale")).toHaveTextContent("fresh rationale");
      expect(screen.queryByTestId("dispatch-proposal-repropose")).not.toBeInTheDocument();

      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(dispatched).toMatchObject({ title: "fresh-title", description: "edited brief" });
    });

    it("repropose disables itself and shows the re-proposing label while the fresh call is in flight", async () => {
      let resolveRepropose!: () => void;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal({ title: "first" })),
        ),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("original brief");
      await screen.findByTestId("dispatch-proposal-confirm");

      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, async () => {
          await new Promise<void>((res) => { resolveRepropose = res; });
          return HttpResponse.json(readyProposal({ title: "second" }));
        }),
      );
      await user.click(screen.getByTestId("summary-source-toggle"));
      const textarea = screen.getByTestId("summary-textarea");
      await user.clear(textarea);
      await user.type(textarea, "edited brief");
      await user.tab();
      const repropose = await screen.findByTestId("dispatch-proposal-repropose");
      expect(repropose).toHaveTextContent("Re-propose");

      await user.click(repropose);
      await waitFor(() => expect(screen.queryByTestId("dispatch-proposal-repropose")).not.toBeInTheDocument());
      expect(screen.getByTestId("build-plan")).toBeDisabled();
      expect(screen.getByTestId("build-plan")).toHaveTextContent("Preparing…");
      resolveRepropose();
      await screen.findByTestId("dispatch-proposal-confirm");
    });
  });

  describe("Pending state", () => {
    it("shows the pending line right after Summarize, with no click needed, before the auto-triggered propose settles", async () => {
      let resolveExtract!: () => void;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, async () => {
          await new Promise<void>((res) => { resolveExtract = res; });
          return HttpResponse.json(readyProposal());
        }),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await waitFor(() => expect(screen.getByTestId("proposal-panel-pending")).toBeInTheDocument());
      expect(screen.getByTestId("build-plan")).toBeDisabled();
      expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("Preparing…");
      resolveExtract();
      await screen.findByTestId("dispatch-proposal-confirm");
    });

    it("shows the questions body's own busy label while re-proposing after answering, not the generic pending line", async () => {
      let resolveReAsk!: () => void;
      let extractCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, async () => {
          extractCalls += 1;
          if (extractCalls === 1) {
            return HttpResponse.json({ status: "need_more_info", questions: ["Which database?"] });
          }
          await new Promise<void>((res) => { resolveReAsk = res; });
          return HttpResponse.json(readyProposal({ title: "answered" }));
        }),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
      await user.type(screen.getByTestId("answer-0"), "Postgres");
      await user.click(screen.getByTestId("dispatch-build"));

      await waitFor(() => expect(screen.getByTestId("dispatch-build")).toBeDisabled());
      expect(screen.getByTestId("dispatch-build")).toHaveTextContent("Dispatching…");
      expect(screen.queryByTestId("proposal-panel-pending")).not.toBeInTheDocument();
      expect(screen.getByTestId("answer-0")).toBeInTheDocument();

      resolveReAsk();
      await screen.findByTestId("dispatch-proposal-confirm");
    });
  });

  describe("Propose failure", () => {
    it("renders a propose failure (502) inline as an alert, with a retry via the build-plan button", async () => {
      let proposeCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => {
          proposeCalls += 1;
          if (proposeCalls === 1) {
            return HttpResponse.json({ detail: "claude failed" }, { status: 502 });
          }
          return HttpResponse.json(readyProposal({ title: "recovered" }));
        }),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("502"));
      const retry = screen.getByTestId("build-plan");
      expect(retry).not.toBeDisabled();
      expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
        "Could not prepare the dispatch proposal.",
      );

      await user.click(retry);
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(proposeCalls).toBe(2);
    });
  });

  describe("Inline questions (no tab switch)", () => {
    it("renders need_more_info directly on the already-selected Summary tab and dispatches from there without ever changing tabs", async () => {
      let created: { title: string; description: string } | null = null;
      let extractCalls = 0;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => {
          extractCalls += 1;
          if (extractCalls === 1) {
            return HttpResponse.json({ status: "need_more_info", questions: ["Which region?", "Which tier?"] });
          }
          return HttpResponse.json(readyProposal({ title: "answered-title" }));
        }),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
        http.post("/api/workspaces", async ({ request }) => {
          created = (await request.json()) as { title: string; description: string };
          return HttpResponse.json({ task_id: "build", root_task_ids: ["build"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await waitFor(() => expect(screen.getByTestId("answer-0")).toBeInTheDocument());
      expect(screen.queryByTestId("tab-questions")).not.toBeInTheDocument();
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");

      await user.type(screen.getByTestId("answer-0"), "eu-west");
      await user.type(screen.getByTestId("answer-1"), "premium");
      await user.click(screen.getByTestId("dispatch-build"));
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.getByTestId("tab-summary")).toHaveAttribute("aria-selected", "true");

      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      expect(await screen.findByTestId("home")).toBeInTheDocument();
      expect(created).not.toBeNull();
      const payload = created as unknown as { title: string; description: string };
      expect(payload.description).toContain("Which region?");
      expect(payload.description).toContain("eu-west");
    });
  });

  describe("Title / acceptance clauses / counter-case rendered inline", () => {
    it("shows the title, every acceptance clause, and all three counter-case fields once a proposal is ready", async () => {
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(
            readyProposal({
              title: "Ship the widget",
              acceptance_clauses: ["clause one", "clause two", "clause three"],
            }),
          ),
        ),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");

      expect(screen.getByTestId("dispatch-proposal-title")).toHaveTextContent("Ship the widget");
      const clauses = screen.getByTestId("dispatch-proposal-acceptance-clauses");
      expect(clauses).toHaveTextContent("clause one");
      expect(clauses).toHaveTextContent("clause two");
      expect(clauses).toHaveTextContent("clause three");
      expect(screen.getByTestId("dispatch-counter-case-against")).toHaveTextContent(COUNTER_CASE.against);
      expect(screen.getByTestId("dispatch-counter-case-cheapest-80")).toHaveTextContent(
        COUNTER_CASE.cheapest_80,
      );
      expect(screen.getByTestId("dispatch-counter-case-cost-estimate")).toHaveTextContent(
        COUNTER_CASE.cost_estimate,
      );
      expect(screen.getByTestId("dispatch-proposal-confirm")).toHaveAttribute(
        "aria-describedby",
        "dispatch-counter-case",
      );
    });

    it("shows the blocking count and a recorded group, and creates the recorded clause with the record kind on confirm", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(
            readyProposal({ acceptance_clauses: ["clause one"], recorded_clauses: ["the docs say X"] }),
          ),
        ),
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.getByText("Blocking (1)")).toBeInTheDocument();
      expect(screen.getByText("Recorded (1)")).toBeInTheDocument();
      expect(screen.getByTestId("dispatch-proposal-recorded-clauses")).toHaveTextContent("the docs say X");

      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      await screen.findByTestId("home");
      const requirements = (dispatched as unknown as { requirements: object[] }).requirements;
      expect(requirements).toEqual([{ text: "clause one" }, { text: "the docs say X", kind: "record" }]);
      expect(requirements[0]).not.toHaveProperty("kind");
    });

    it("omits the acceptance-clauses list when the proposal carries none", async () => {
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () =>
          HttpResponse.json(readyProposal({ acceptance_clauses: [] })),
        ),
      );
      renderBuildChat();
      await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");
      expect(screen.queryByTestId("dispatch-proposal-acceptance-clauses")).not.toBeInTheDocument();
    });

    it("the instructions disclosure is closed by default, and typed instructions are trimmed and forwarded on confirm", async () => {
      let dispatched: Record<string, unknown> | null = null;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, () => HttpResponse.json(readyProposal())),
        http.post("/api/workspaces", async ({ request }) => {
          dispatched = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ task_id: "t", root_task_ids: ["t"] });
        }),
        http.delete(/\/api\/build-chat\/sessions\/[^/]+$/, () => new HttpResponse(null, { status: 204 })),
      );
      renderBuildChat();
      const user = await summarizeTo("Build the X feature");
      await screen.findByTestId("dispatch-proposal-confirm");

      const details = screen.getByText("Instructions (optional)").closest("details");
      expect(details).not.toHaveAttribute("open");
      expect(screen.queryByTestId("proposal-panel-instructions")).not.toBeVisible();

      await user.click(screen.getByText("Instructions (optional)"));
      await user.type(screen.getByTestId("proposal-panel-instructions"), "  Focus on the read path.  ");
      await user.click(screen.getByTestId("dispatch-proposal-confirm"));
      await screen.findByTestId("home");
      expect(dispatched).toMatchObject({ instructions: "Focus on the read path." });
    });
  });

  describe("Status live region", () => {
    it("carries the right announcement through idle -> pending -> ready -> stale", async () => {
      let resolveExtract!: () => void;
      server.use(
        http.post(`${BUILD_CHAT_PATH}/${SID}/propose`, async () => {
          await new Promise<void>((res) => { resolveExtract = res; });
          return HttpResponse.json(readyProposal());
        }),
        http.put(`${BUILD_CHAT_PATH}/${SID}/brief`, async ({ request }) => {
          const body = (await request.json()) as { content: string };
          return HttpResponse.json({ content: body.content, updated_at: "2026-05-01T02:00:00Z" });
        }),
      );
      const user = userEvent.setup();
      renderBuildChat();
      await waitFor(() => expect(screen.getByTestId("tab-summary")).toBeInTheDocument());
      await user.click(screen.getByTestId("tab-summary"));
      expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("");
      await user.click(screen.getByTestId("tab-chat"));

      await summarizeTo("original brief");
      expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent("Preparing…");

      resolveExtract();
      await waitFor(() =>
        expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
          "Proposal ready to dispatch.",
        ),
      );

      await user.click(screen.getByTestId("summary-source-toggle"));
      const textarea = screen.getByTestId("summary-textarea");
      await user.clear(textarea);
      await user.type(textarea, "edited brief");
      await user.tab();
      await waitFor(() =>
        expect(screen.getByTestId("proposal-panel-status")).toHaveTextContent(
          "Brief changed since this proposal — re-propose to see the refreshed one.",
        ),
      );
    });
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    nodeTypes,
  }: {
    nodes: { id: string; type: string; data: Record<string, unknown> }[];
    nodeTypes: Record<
      string,
      React.ComponentType<{ data: Record<string, unknown> }>
    >;
  }) {
    return React.createElement(
      "div",
      { "data-testid": "rf-mock" },
      nodes.map((n) => {
        const Component = nodeTypes[n.type]!;
        return React.createElement(Component, { key: n.id, data: n.data });
      }),
    );
  }
  function Stub() {
    return null;
  }
  return { ReactFlow, Background: Stub, Controls: Stub };
});

import { expectDisclosureState } from "../../../test/disclosure";
import { server } from "../../../test/server";
import { BASE, installWorkspaceDefaultHandlers, noEventsHandler, renderAt } from "../../../test/workspacePageHarness";

function stubNonDefaultEngine() {
  server.use(
    http.get("/api/engines", () =>
      HttpResponse.json({
        engines: [
          { repo: { owner: "DejvMatikar", repo: "alan" } },
          { repo: { owner: "acme", repo: "sprocket" } },
        ],
      }),
    ),
  );
}

installWorkspaceDefaultHandlers();

describe("Workspace detail", () => {
  it("renders the header with title and status badge", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByRole("heading", { level: 1, name: "T1 Title" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("status-active")).toBeInTheDocument();
  });

  it("renders no engine badge for a non-default engine_key when only the default engine exists", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, engine_key: "acme/sprocket" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument();
  });

  it("renders an engine badge next to the status badge once a second repo exists", async () => {
    stubNonDefaultEngine();
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, engine_key: "acme/sprocket" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const badge = await screen.findByTestId("engine-badge-t1");
    expect(badge).toHaveTextContent("acme/sprocket");
  });

  it("hides the engine badge once a refetch reports the workspace back on the default engine", async () => {
    stubNonDefaultEngine();
    let call = 0;
    server.use(
      http.get("/api/workspaces/t1", () => {
        call += 1;
        return HttpResponse.json(
          call === 1 ? { ...BASE, engine_key: "acme/sprocket" } : { ...BASE, status: "archived" },
        );
      }),
      noEventsHandler(),
      http.post("/api/workspaces/t1/archive", () => new HttpResponse(null, { status: 204 })),
    );
    renderAt();
    await screen.findByTestId("engine-badge-t1");
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(screen.queryByTestId("engine-badge-t1")).not.toBeInTheDocument());
  });

  it("surfaces per-task definition-of-done and the split rationale", async () => {
    const doc = {
      ...BASE,
      tasks: [
        {
          id: "be",
          depends_on: [],
          team: "implementation",
          status: "completed",
          planning_state: "detailed",
          dod_clauses: [
            { id: "dod-1", text: "endpoint works", covers: ["req-a"], evidence: "PR #5" },
          ],
        },
      ],
      latest_handover: { split_rationale: "split into backend + frontend" },
    };
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(doc)),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByText("Definition of done")).toBeInTheDocument();
    expect(screen.getByTestId("task-dod-be")).toBeInTheDocument();
    expect(screen.getByText("Decomposition")).toBeInTheDocument();
    expect(screen.getByTestId("split-rationale")).toHaveTextContent(
      "split into backend + frontend",
    );
  });

  it("omits the DoD and decomposition sections when there is no such data", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByText("Definition of done")).toBeNull();
    expect(screen.queryByTestId("split-rationale")).toBeNull();
  });

  it("renders the task description collapsed on arrival and opens it on click", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    const content = screen.getByTestId("workspace-task-description");
    const details = screen.getByTestId("workspace-task-description-disclosure");
    await expectDisclosureState(details, false);
    expect(content).not.toBeVisible();

    await userEvent.click(screen.getByText("Task description"));

    await expectDisclosureState(details, true);
    expect(content).toBeVisible();
    expect(content).toHaveTextContent("do it");
  });

  it("renders no prep-chat link when the workspace has no archived conversation", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByTestId("prep-chat-link")).not.toBeInTheDocument();
  });

  it("renders a labelled prep-chat link above the task-description panel for a build chat", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "build", consultant_key: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    const link = screen.getByTestId("prep-chat-link");
    expect(link).toHaveTextContent("View preparation chat →");
    expect(link).not.toHaveTextContent(/archived/i);
    expect(link).toHaveAttribute("href", "/workspaces/t1/prep-chat");

    const panel = screen.getByText("Task description");
    expect(
      link.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("names the consultant on the prep-chat link for an explore chat", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "explore", consultant_key: "brainstorming" },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(await screen.findByText("View Brainstorming conversation →")).toBeInTheDocument();
  });

  it("falls back to a generic label on the prep-chat link when the consultant key is missing", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "explore", consultant_key: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.getByTestId("prep-chat-link")).toHaveTextContent("View consultant conversation →");
  });

  it("falls back to the raw consultant key on the prep-chat link when unknown", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "explore", consultant_key: "mystery_key" },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.getByTestId("prep-chat-link")).toHaveTextContent(
      "View mystery_key conversation →",
    );
  });

  it("gives the prep-chat link the body-link recipe with a 44px target", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "build", consultant_key: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const link = await screen.findByTestId("prep-chat-link");
    expect(link.className).toContain("text-btn-action underline");
    expect(link.className).toContain("min-h-11");
  });

  it("reveals a description of the prep-chat link on keyboard focus", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({
          ...BASE,
          prep_chat: { session_id: "s1", kind: "build", consultant_key: null },
        }),
      ),
      noEventsHandler(),
    );
    renderAt();
    const link = await screen.findByTestId("prep-chat-link");
    link.focus();
    expect(link).toHaveAccessibleDescription(
      "Opens the conversation this workspace was set up from.",
    );
  });

  it("shows the Deliverables section when the latest handover has artifacts_produced", async () => {
    const doc = {
      ...BASE,
      latest_handover: {
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: null,
      },
    };
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(doc)),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByRole("heading", { name: "Deliverables" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Full research report" }),
    ).toBeInTheDocument();
  });

  it("scopes the Deliverables PR link to the workspace's own non-default engine_key", async () => {
    const doc = {
      ...BASE,
      engine_key: "acme-corp/sprocket",
      latest_handover: {
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: 42,
      },
    };
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(doc)),
      noEventsHandler(),
    );
    renderAt();
    expect(await screen.findByTestId("pr-link")).toHaveAttribute(
      "href",
      "https://github.com/acme-corp/sprocket/pull/42",
    );
  });

  it("renders a degraded, non-clickable Deliverables PR reference when the workspace has no engine_key", async () => {
    const doc = {
      ...BASE,
      latest_handover: {
        artifacts_produced: [
          { path: "invocations/inv_001/report.md", description: "Full research report" },
        ],
        pr_number: 42,
      },
    };
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(doc)),
      noEventsHandler(),
    );
    renderAt();
    const el = await screen.findByTestId("pr-link");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveTextContent("repository unresolved");
  });

  it("omits the Deliverables section when there is no latest handover", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });
    expect(screen.queryByRole("heading", { name: "Deliverables" })).toBeNull();
  });

  it("falls back to task_id when title is blank", async () => {
    server.use(
      http.get("/api/workspaces/t1", () =>
        HttpResponse.json({ ...BASE, title: "" }),
      ),
      noEventsHandler(),
    );
    renderAt();
    expect(
      await screen.findByRole("heading", { level: 1, name: "t1" }),
    ).toBeInTheDocument();
  });

  it("lays the aside out as a second wide-screen grid column beside the main content", async () => {
    server.use(
      http.get("/api/workspaces/t1", () => HttpResponse.json(BASE)),
      noEventsHandler(),
    );
    renderAt();
    await screen.findByRole("heading", { level: 1, name: "T1 Title" });

    const columns = screen.getByTestId("workspace-columns");
    expect(columns.className).toContain("grid");
    expect(columns.className).toMatch(/sm:grid-cols-\[/);

    const aside = screen.getByTestId("workspace-details-aside");
    expect(columns.contains(aside)).toBe(true);
    expect(aside.parentElement).not.toBe(
      screen.getByTestId("workspace-situation-slot").parentElement,
    );
  });
});

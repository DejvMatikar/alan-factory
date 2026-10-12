import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import NextItemProposal from "./NextItemProposal";

const PROPOSAL = {
  fingerprint: null,
  workspace_id: null,
  task_id: null,
  project_id: "proj-1",
  engine_key: "DejvMatikar/alan",
  title: "Next: Ship the widget",
  prepared_context: "Build the widget.",
  recommendation: "All prior roadmap items have delivered — start it next.",
  actions: [
    {
      kind: "direct_dispatch",
      label: "Start: Ship the widget",
      team: null,
      instructions: null,
      url: "/api/projects/proj-1/roadmap/advance",
    },
  ],
  updated_at: "2026-07-08T00:00:00Z",
  autonomy: "propose",
};

function mockProposal(body: Record<string, unknown>) {
  server.use(
    http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => HttpResponse.json(body)),
  );
}

describe("NextItemProposal", () => {
  it("renders nothing and fetches nothing when autonomy is auto_continue", () => {
    let called = false;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        called = true;
        return HttpResponse.json({ proposal: null, budget_state: "normal" });
      }),
    );
    const { container } = render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="auto_continue"
        roadmapDelivered={0}
        roadmapTotal={0}
      />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(called).toBe(false);
  });

  it("shows the recommendation with a one-tap start action", async () => {
    mockProposal({ proposal: PROPOSAL, budget_state: "normal" });
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={1}
        roadmapTotal={2}
      />,
    );
    expect(await screen.findByTestId("next-item-proposal")).toBeInTheDocument();
    expect(screen.getByTestId("next-item-proposal-title")).toHaveTextContent(
      "Next: Ship the widget",
    );
    expect(screen.getByTestId("next-item-proposal-start")).toHaveTextContent(
      "Start: Ship the widget",
    );
  });

  it("falls back to a generic start label when the proposal carries no actions", async () => {
    mockProposal({ proposal: { ...PROPOSAL, actions: [] }, budget_state: "normal" });
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={1}
        roadmapTotal={2}
      />,
    );
    expect(await screen.findByTestId("next-item-proposal-start")).toHaveTextContent(
      "Start: Next: Ship the widget",
    );
  });

  it("renders a distinct nothing-left-to-start message once every item has delivered", async () => {
    mockProposal({ proposal: null, budget_state: "normal" });
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={2}
        roadmapTotal={2}
      />,
    );
    expect(await screen.findByTestId("next-item-proposal-none")).toBeInTheDocument();
  });

  it("renders nothing when budget is exhausted, even with items remaining", async () => {
    mockProposal({ proposal: null, budget_state: "paused" });
    const { container } = render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={0}
        roadmapTotal={2}
      />,
    );
    await waitFor(() => {
      expect(container.textContent).toBe("");
    });
  });

  it("renders nothing when there is no roadmap imported at all", async () => {
    mockProposal({ proposal: null, budget_state: "normal" });
    const { container } = render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={0}
        roadmapTotal={0}
      />,
    );
    await waitFor(() => {
      expect(container.textContent).toBe("");
    });
  });

  it("shows an error state when the fetch fails", async () => {
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => HttpResponse.error()),
    );
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={0}
        roadmapTotal={1}
      />,
    );
    expect(await screen.findByTestId("next-item-proposal-error")).toBeInTheDocument();
  });

  it("starting the item disables the button and the card clears once the item is no longer proposable", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () => {
        getCalls += 1;
        return HttpResponse.json({
          proposal: getCalls === 1 ? PROPOSAL : null,
          budget_state: "normal",
        });
      }),
      http.post("/api/projects/proj-1/roadmap/advance", () =>
        HttpResponse.json(
          { task_id: "ws-1", item_id: "item-1", item_title: "Item" },
          { status: 201 },
        ),
      ),
    );
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={1}
        roadmapTotal={2}
      />,
    );
    const start = await screen.findByTestId("next-item-proposal-start");
    fireEvent.click(start);
    await waitFor(() => {
      expect(screen.queryByTestId("next-item-proposal")).not.toBeInTheDocument();
    });
    expect(getCalls).toBe(2);
  });

  it("shows an inline error when advancing fails, and keeps the card visible", async () => {
    server.use(
      http.get("/api/projects/proj-1/roadmap/next-item-proposal", () =>
        HttpResponse.json({ proposal: PROPOSAL, budget_state: "normal" }),
      ),
      http.post("/api/projects/proj-1/roadmap/advance", () =>
        HttpResponse.json(
          { detail: "no un-attempted roadmap item to advance to" },
          { status: 409 },
        ),
      ),
    );
    render(
      <NextItemProposal
        projectId="proj-1"
        autonomy="propose"
        roadmapDelivered={1}
        roadmapTotal={2}
      />,
    );
    const start = await screen.findByTestId("next-item-proposal-start");
    fireEvent.click(start);
    expect(await screen.findByTestId("next-item-proposal-advance-error")).toHaveTextContent(
      "409",
    );
    expect(screen.getByTestId("next-item-proposal")).toBeInTheDocument();
  });
});

import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { UnblockScopedActivity } from "../../../api";
import { ServerEventsContext } from "../../../api/ServerEventsProvider";
import { server } from "../../../test/server";
import UnblockerSummaryRow from "./UnblockerSummaryRow";

function scoped(overrides: Partial<UnblockScopedActivity> = {}): UnblockScopedActivity {
  return {
    scope: "repo",
    repo: "acme/widget",
    window_days: 30,
    events_total: 6,
    workspaces_touched: 4,
    waited_workspaces: 0,
    diagnosed_workspaces: 0,
    diagnosable_workspaces: 0,
    still_stuck_workspaces: 0,
    gave_up_workspaces: 0,
    aggregates: [],
    fleet_ever_recorded: true,
    events: [],
    workspaces: [],
    ...overrides,
  };
}

function serve(payload: UnblockScopedActivity, engines: (string | null)[] = []): void {
  server.use(
    http.get("/api/health/unblocker-activity", ({ request }) => {
      engines.push(new URL(request.url).searchParams.get("engine"));
      return HttpResponse.json(payload);
    }),
  );
}

function renderRow() {
  return render(
    <MemoryRouter>
      <UnblockerSummaryRow engineKey="acme/widget" />
    </MemoryRouter>,
  );
}

describe("UnblockerSummaryRow", () => {
  it("renders nothing while the activity is still loading", () => {
    server.use(http.get("/api/health/unblocker-activity", () => new Promise(() => undefined)));
    const { container } = renderRow();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the load fails", async () => {
    let requested = false;
    server.use(
      http.get("/api/health/unblocker-activity", () => {
        requested = true;
        return HttpResponse.json({ detail: "boom" }, { status: 500 });
      }),
    );
    const { container } = renderRow();
    await waitFor(() => {
      expect(requested).toBe(true);
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("stops rendering the last good counts once a refetch fails", async () => {
    let failing = false;
    server.use(
      http.get("/api/health/unblocker-activity", () =>
        failing ? HttpResponse.error() : HttpResponse.json(scoped()),
      ),
    );
    const tree = (health: number) =>
      createElement(
        ServerEventsContext.Provider,
        { value: { counters: { workspace_list: 0, health, engines: 0 }, connected: true } },
        createElement(MemoryRouter, null, createElement(UnblockerSummaryRow, { engineKey: "acme/widget" })),
      );
    const { rerender } = render(tree(0));
    await screen.findByTestId("repo-status-unblocker-acme/widget");
    failing = true;
    rerender(tree(1));
    await waitFor(() => {
      expect(screen.queryByTestId("repo-status-unblocker-acme/widget")).not.toBeInTheDocument();
    });
  });

  it("renders nothing when the repository has no unblocker activity in the window", async () => {
    const engines: (string | null)[] = [];
    serve(scoped({ events_total: 0, workspaces_touched: 0, waited_workspaces: 2 }), engines);
    const { container } = renderRow();
    await waitFor(() => {
      expect(engines).toEqual(["acme/widget"]);
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("fetches the scoped payload for the repository in view", async () => {
    const engines: (string | null)[] = [];
    serve(scoped(), engines);
    renderRow();
    await screen.findByTestId("repo-status-unblocker-acme/widget");
    expect(engines).toEqual(["acme/widget"]);
  });

  it("shows the two quiet counts, neutral, when nothing is stuck or given up", async () => {
    serve(scoped());
    renderRow();
    await screen.findByTestId("repo-status-unblocker-acme/widget");
    expect(screen.getByTestId("aggregate-chip-acme/widget-unblocker-events")).toHaveTextContent("6");
    expect(screen.getByTestId("aggregate-chip-acme/widget-unblocker-touched")).toHaveTextContent("4");
    expect(screen.getByTestId("aggregate-chip-acme/widget-unblocker-events").className).toContain(
      "status-neutral",
    );
    expect(screen.queryByTestId("repo-status-group-acme/widget-unblocker-stuck")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-status-group-acme/widget-unblocker-gave-up")).not.toBeInTheDocument();
  });

  it("shows the still-stuck and gave-up counts in red when they are non-zero", async () => {
    serve(scoped({ still_stuck_workspaces: 2, gave_up_workspaces: 1 }));
    renderRow();
    await screen.findByTestId("repo-status-unblocker-acme/widget");
    const stuck = screen.getByTestId("aggregate-chip-acme/widget-unblocker-stuck");
    const gaveUp = screen.getByTestId("aggregate-chip-acme/widget-unblocker-gave-up");
    expect(stuck).toHaveTextContent("2");
    expect(gaveUp).toHaveTextContent("1");
    expect(stuck.className).toContain("status-danger");
    expect(gaveUp.className).toContain("status-danger");
  });

  it("labels every count so the chip is not read as a bare number", async () => {
    serve(scoped({ still_stuck_workspaces: 2, gave_up_workspaces: 1 }));
    renderRow();
    const row = await screen.findByTestId("repo-status-unblocker-acme/widget");
    for (const label of ["Unblocker events", "Workspaces touched", "Still stuck", "Gave up"]) {
      expect(within(row).getByText(label)).toBeInTheDocument();
    }
  });

  it("links every chip to that repository's scoped health page with the standard tap height", async () => {
    serve(scoped({ still_stuck_workspaces: 2, gave_up_workspaces: 1 }));
    renderRow();
    const row = await screen.findByTestId("repo-status-unblocker-acme/widget");
    const links = within(row).getAllByRole("link");
    expect(links).toHaveLength(4);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/health?engine=acme%2Fwidget");
      expect(link.className).toContain("min-h-11");
    }
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http, type JsonBodyType } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { ChangeKind } from "../../../api";
import { ServerEventsContext } from "../../../api/ServerEventsProvider";
import { server } from "../../../test/server";
import OperatorNavLink from "./OperatorNavLink";

const IDLE = { session: null, unavailable: null };
const LIVE = {
  session: {
    scope: "fleet",
    target: null,
    started_at: "2026-09-29T10:00:00Z",
    update_warning: null,
    exited: false,
  },
  unavailable: null,
};

function counters(health: number): Record<ChangeKind, number> {
  return { workspace_list: 0, health, engines: 0 };
}

function tree(health: number) {
  return (
    <ServerEventsContext.Provider value={{ counters: counters(health), connected: true }}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<OperatorNavLink />} />
          <Route path="/operator" element={<div data-testid="operator-page">operator</div>} />
        </Routes>
      </MemoryRouter>
    </ServerEventsContext.Provider>
  );
}

function serveStatus(bodies: JsonBodyType[]) {
  const tally = { calls: 0 };
  server.use(
    http.get("/api/operator/status", () => {
      const body = bodies[Math.min(tally.calls, bodies.length - 1)];
      tally.calls += 1;
      return HttpResponse.json(body);
    }),
  );
  return tally;
}

describe("OperatorNavLink", () => {
  it("shows an unlit dot and a plain label while no session is live", async () => {
    serveStatus([IDLE]);
    render(tree(0));
    const link = await screen.findByTestId("nav-operator");
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "false");
    });
    expect(link).toHaveAttribute("aria-label", "Operator terminal");
    expect(link).toHaveTextContent("Operator");
    expect(screen.getByTestId("nav-operator-dot").className).not.toContain("bg-green-500");
  });

  it("lights the dot and says so in the accessible name while a session is live", async () => {
    serveStatus([LIVE]);
    render(tree(0));
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
    });
    expect(screen.getByTestId("nav-operator-dot").className).toContain("bg-green-500");
    expect(screen.getByTestId("nav-operator")).toHaveAttribute(
      "aria-label",
      "Operator terminal: session live",
    );
  });

  it("hides the dot from assistive technology", async () => {
    serveStatus([IDLE]);
    render(tree(0));
    expect(await screen.findByTestId("nav-operator-dot")).toHaveAttribute("aria-hidden", "true");
  });

  it("refetches the status when the health change counter moves", async () => {
    const tally = serveStatus([IDLE, LIVE]);
    const { rerender } = render(tree(0));
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "false");
    });
    expect(tally.calls).toBe(1);
    rerender(tree(1));
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
    });
    expect(tally.calls).toBe(2);
  });

  it("keeps the last known dot when a refetch fails", async () => {
    let calls = 0;
    server.use(
      http.get("/api/operator/status", () => {
        calls += 1;
        return calls === 1 ? HttpResponse.json(LIVE) : HttpResponse.error();
      }),
    );
    const { rerender } = render(tree(0));
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
    });
    rerender(tree(1));
    await waitFor(() => {
      expect(calls).toBe(2);
    });
    expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
  });

  it("links to the operator page and navigates there on click", async () => {
    serveStatus([IDLE]);
    render(tree(0));
    const link = await screen.findByTestId("nav-operator");
    expect(link).toHaveAttribute("href", "/operator");
    await userEvent.setup().click(link);
    expect(await screen.findByTestId("operator-page")).toBeInTheDocument();
  });

  it("never asks for an attach ticket, so nothing reattaches until the page is opened", async () => {
    serveStatus([LIVE]);
    let tickets = 0;
    server.use(
      http.post("/api/operator/attach-ticket", () => {
        tickets += 1;
        return HttpResponse.json({ ticket: "unused", expires_in_s: 30 });
      }),
    );
    render(tree(0));
    await waitFor(() => {
      expect(screen.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
    });
    expect(tickets).toBe(0);
  });

  it("meets the 44px minimum tap-target size", async () => {
    serveStatus([IDLE]);
    render(tree(0));
    const link = await screen.findByTestId("nav-operator");
    expect(link.className).toContain("min-h-11");
    expect(link.className).toContain("min-w-11");
  });

  it("carries a plain-language description revealed on hover or focus", async () => {
    serveStatus([IDLE]);
    render(tree(0));
    const link = await screen.findByTestId("nav-operator");
    const describedBy = link.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "A Claude Code terminal for diagnosing and unblocking the fleet.",
    );
  });
});

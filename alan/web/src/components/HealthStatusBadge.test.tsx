import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { HealthProvider } from "../api/HealthProvider";
import { server } from "../test/server";
import HealthStatusBadge from "./HealthStatusBadge";

const BASE_HEALTH = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
  health_state: null,
};

const HEALTHY_STATE = {
  verdict: "healthy" as const,
  blocked_count: 0,
  in_flight_count: 0,
  findings: [],
  computed_at: new Date().toISOString(),
};

function degradedState(blockedCount: number, inFlightCount: number) {
  return {
    verdict: "degraded" as const,
    blocked_count: blockedCount,
    in_flight_count: inFlightCount,
    findings: [],
    computed_at: new Date().toISOString(),
  };
}

function renderBadge(initialEntries = ["/"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <HealthProvider>
        <Routes>
          <Route path="/" element={<HealthStatusBadge />} />
          <Route path="/health" element={<div data-testid="health-page">health</div>} />
        </Routes>
      </HealthProvider>
    </MemoryRouter>,
  );
}

describe("HealthStatusBadge", () => {
  const testId = "nav-health-badge";

  it("renders the healthy state", async () => {
    server.use(
      http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, health_state: HEALTHY_STATE })),
    );
    renderBadge();
    await waitFor(() => {
      expect(screen.getByTestId(`${testId}-dot`).className).toContain("bg-green-500");
    });
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-label", "System health: 0 issues");
  });

  it("renders the stale state when health_state is null", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    await waitFor(() => {
      expect(screen.getByTestId(`${testId}-dot`).className).toContain("bg-gray-400");
    });
    expect(screen.getByTestId(testId)).toHaveAttribute(
      "aria-label",
      "System health: Status unknown",
    );
  });

  it("renders the paused state", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ok: true,
          queue_paused: true,
          paused_until: Date.now() / 1000 + 3600,
          health_state: HEALTHY_STATE,
        }),
      ),
    );
    renderBadge();
    await waitFor(() => {
      expect(screen.getByTestId(`${testId}-dot`).className).toContain("bg-amber-400");
    });
    expect(screen.getByTestId(testId)).toHaveAttribute("aria-label", "System health: Paused");
  });

  it("renders the degraded state with issue and needs-you counts", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: degradedState(3, 1) }),
      ),
    );
    renderBadge();
    await waitFor(() => {
      expect(screen.getByTestId(`${testId}-dot`).className).toContain("bg-red-500");
    });
    expect(screen.getByTestId(testId)).toHaveAttribute(
      "aria-label",
      "System health: 3 issues · 2 needs you",
    );
  });

  it("links to /health", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    await waitFor(() => {
      expect(screen.getByTestId(testId)).toHaveAttribute("href", "/health");
    });
  });

  it("navigates to the health page on click", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId(testId));
    expect(await screen.findByTestId("health-page")).toBeInTheDocument();
  });

  it("meets the 44px minimum tap-target size", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    const link = await screen.findByTestId(testId);
    expect(link.className).toContain("min-h-11");
    expect(link.className).toContain("min-w-11");
  });
});

describe("HealthStatusBadge — text and count pill", () => {
  it("shows the compact 'Health' label instead of the full state text", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: degradedState(3, 1) }),
      ),
    );
    renderBadge();
    const link = await screen.findByTestId("nav-health-badge");
    await waitFor(() => expect(link).toHaveTextContent("Health"));
    expect(link).not.toHaveTextContent("3 issues");
  });

  it("shows a count pill when degraded", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: degradedState(4, 0) }),
      ),
    );
    renderBadge();
    const pill = await screen.findByTestId("nav-health-badge-count");
    expect(pill).toHaveTextContent("4");
  });

  it("hides the count pill when healthy", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    await screen.findByTestId("nav-health-badge");
    expect(screen.queryByTestId("nav-health-badge-count")).not.toBeInTheDocument();
  });

  it("desaturates the count pill instead of using the destructive-red treatment", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...BASE_HEALTH, health_state: degradedState(4, 0) }),
      ),
    );
    renderBadge();
    const pill = await screen.findByTestId("nav-health-badge-count");
    expect(pill.className).not.toContain("bg-btn-destroy");
    expect(pill.className).toContain("bg-status-neutral-tint");
  });

  it("carries a hover/focus-revealed plain-language description of what the control does", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderBadge();
    const link = await screen.findByTestId("nav-health-badge");
    const describedBy = link.getAttribute("aria-describedby");
    expect(describedBy).not.toBeNull();
    expect(document.getElementById(describedBy ?? "")).toHaveTextContent(
      "Overall system health across every connected repository.",
    );
  });
});

import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { HealthProvider } from "../../../api/HealthProvider";
import { server } from "../../../test/server";
import TriageSweepCard from "./TriageSweepCard";

const BASE_HEALTH = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
  health_state: null,
};

function renderCard() {
  return render(
    <HealthProvider>
      <TriageSweepCard />
    </HealthProvider>,
  );
}

describe("TriageSweepCard", () => {
  it("shows a never-run state before the health fetch resolves", () => {
    server.use(http.get("/api/health", () => new Promise(() => undefined)));
    renderCard();
    expect(screen.getByTestId("triage-sweep-card")).toBeInTheDocument();
    expect(screen.getByTestId("triage-sweep-never-run")).toBeInTheDocument();
    expect(screen.getByTestId("triage-sweep-surface-count")).toHaveTextContent("—");
    expect(screen.getByTestId("triage-sweep-blocked-count")).toHaveTextContent("—");
  });

  it("shows a never-run state when the sweeper has never completed a sweep", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: null,
            surface_count: null,
            blocked_count: null,
            next_eligible_utc: null,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    expect(await screen.findByTestId("triage-sweep-never-run")).toBeInTheDocument();
    expect(screen.queryByTestId("triage-sweep-freshness")).not.toBeInTheDocument();
  });

  it("renders a real zero count distinctly from the never-run dash", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: "2026-07-11T12:00:00Z",
            surface_count: 0,
            blocked_count: 0,
            next_eligible_utc: null,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    expect(await screen.findByTestId("triage-sweep-surface-count")).toHaveTextContent("0");
    expect(screen.getByTestId("triage-sweep-blocked-count")).toHaveTextContent("0");
    expect(screen.queryByTestId("triage-sweep-never-run")).not.toBeInTheDocument();
  });

  it("shows non-zero counts and relative-time freshness with a native title tooltip", async () => {
    const ts = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: ts,
            surface_count: 7,
            blocked_count: 2,
            next_eligible_utc: null,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    expect(await screen.findByTestId("triage-sweep-surface-count")).toHaveTextContent("7");
    expect(screen.getByTestId("triage-sweep-blocked-count")).toHaveTextContent("2");
    const freshness = screen.getByTestId("triage-sweep-freshness");
    expect(freshness).toHaveTextContent("Swept 5 minutes ago");
    expect(freshness).toHaveAttribute("title", new Date(ts).toLocaleString());
    expect(screen.queryByTestId("triage-sweep-stale")).not.toBeInTheDocument();
  });

  it("flags a stale sweep once the swept timestamp is older than the staleness threshold", async () => {
    const ts = new Date(Date.now() - 90 * 60 * 1000).toISOString();
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: ts,
            surface_count: 3,
            blocked_count: 1,
            next_eligible_utc: null,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    expect(await screen.findByTestId("triage-sweep-stale")).toBeInTheDocument();
  });

  it("does not flag staleness when the sweeper has never run", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderCard();
    expect(await screen.findByTestId("triage-sweep-never-run")).toBeInTheDocument();
    expect(screen.queryByTestId("triage-sweep-stale")).not.toBeInTheDocument();
  });

  it("shows the next eligible sweep time when present", async () => {
    const nextEligible = "2026-07-11T13:00:00Z";
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: "2026-07-11T12:00:00Z",
            surface_count: 3,
            blocked_count: 0,
            next_eligible_utc: nextEligible,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    const el = await screen.findByTestId("triage-sweep-next-eligible");
    expect(el).toHaveTextContent(new Date(nextEligible).toLocaleString());
  });

  it("omits the next eligible sweep time when absent", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: "2026-07-11T12:00:00Z",
            surface_count: 3,
            blocked_count: 0,
            next_eligible_utc: null,
            recent_actions: [],
          },
        }),
      ),
    );
    renderCard();
    await screen.findByTestId("triage-sweep-card");
    expect(screen.queryByTestId("triage-sweep-next-eligible")).not.toBeInTheDocument();
  });

  it("shows a clean empty state when no recent triage-audit actions are recorded", async () => {
    server.use(http.get("/api/health", () => HttpResponse.json(BASE_HEALTH)));
    renderCard();
    expect(await screen.findByTestId("triage-sweep-actions-empty")).toHaveTextContent(
      "No recent activity",
    );
    expect(screen.queryByTestId("triage-sweep-actions")).not.toBeInTheDocument();
  });

  it("renders up to the last ~10 recorded triage-audit actions", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({
          ...BASE_HEALTH,
          sweeper: {
            timestamp_utc: "2026-07-11T12:00:00Z",
            surface_count: 3,
            blocked_count: 0,
            next_eligible_utc: null,
            recent_actions: [
              { action: "sweep_complete", timestamp_utc: "2026-07-11T12:00:00Z" },
              { action: "invocation_slots_pruned", timestamp_utc: "2026-07-11T11:30:00Z" },
            ],
          },
        }),
      ),
    );
    renderCard();
    const list = await screen.findByTestId("triage-sweep-actions");
    expect(list).toHaveTextContent("sweep complete");
    expect(list).toHaveTextContent("invocation slots pruned");
    expect(screen.queryByTestId("triage-sweep-actions-empty")).not.toBeInTheDocument();
  });
});

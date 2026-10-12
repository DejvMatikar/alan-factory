import { render, screen, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { HealthContext, HealthProvider } from "../api/HealthProvider";
import type { DispatchReadinessIssue, HealthContextValue } from "../api/HealthProvider";
import { server } from "../test/server";
import DispatchReadinessBanner from "./DispatchReadinessBanner";
import QueuePausedBanner from "./QueuePausedBanner";

const MISSING_REVIEWER: DispatchReadinessIssue = {
  engine: "acme/widget",
  requirement: "reviewer_credential",
  state: "not_ready",
  message: "no reviewer credential is stored",
  fix: "store a reviewer credential for acme/widget",
};
const UNKNOWN_QUALITY: DispatchReadinessIssue = {
  engine: "acme/widget",
  requirement: "code_quality",
  state: "unknown",
  message: "could not be evaluated (OSError)",
  fix: "./setup.sh",
};
const UNCONFIGURED: DispatchReadinessIssue = {
  engine: "acme/gadget",
  requirement: "default_engine",
  state: "not_ready",
  message: "no default profile is installed",
  fix: "./setup.sh",
};
const EXPIRY_WARNING: DispatchReadinessIssue = {
  engine: "acme/widget",
  requirement: "reviewer_credential",
  state: "warning",
  message: "the reviewer credential expires on 2031-02-03",
  fix: "store a reviewer credential for acme/widget",
};
const IDENTICAL_REVIEWER: DispatchReadinessIssue = {
  engine: "acme/widget",
  requirement: "reviewer_credential",
  state: "not_ready",
  message: "the reviewer resolves to the same account as the author",
  fix: "store a reviewer credential for acme/widget",
};
const HEALTHY = { ok: true as const, queue_paused: false, paused_until: null, health_state: null };

function contextValue(overrides: Partial<HealthContextValue> = {}): HealthContextValue {
  return {
    queuePaused: false,
    pausedUntil: null,
    healthState: null,
    blockedCount: 0,
    needsUserCount: 0,
    lastComputedAt: null,
    finisherRate: null,
    qualityMetrics: null,
    sweeper: null,
    refetch: vi.fn(),
    ...overrides,
  };
}

function renderWithContext(overrides: Partial<HealthContextValue>) {
  return render(
    <HealthContext.Provider value={contextValue(overrides)}>
      <DispatchReadinessBanner />
    </HealthContext.Provider>,
  );
}

function renderWithReadiness(dispatchReadiness: readonly DispatchReadinessIssue[]) {
  return renderWithContext({ dispatchReadiness });
}

describe("DispatchReadinessBanner — nothing when ready", () => {
  it("renders nothing for an empty readiness list", () => {
    const { container } = renderWithReadiness([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the context carries no readiness list", () => {
    const { container } = renderWithContext({});
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the health response reports every engine ready", async () => {
    let served = 0;
    server.use(
      http.get("/api/health", () => {
        served += 1;
        return HttpResponse.json({ ...HEALTHY, dispatch_readiness: [] });
      }),
    );
    const { container } = render(
      <HealthProvider>
        <DispatchReadinessBanner />
      </HealthProvider>,
    );
    await vi.waitFor(() => {
      expect(served).toBeGreaterThan(0);
    });
    expect(container).toBeEmptyDOMElement();
  });
});

describe("DispatchReadinessBanner — held", () => {
  it("is a polite status region labelled Dispatch held", () => {
    renderWithReadiness([MISSING_REVIEWER]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveAttribute("aria-live", "polite");
    expect(banner).toHaveTextContent("Dispatch held");
    expect(banner).not.toHaveTextContent("Dispatch readiness unverified");
  });

  it("names the engine, the requirement, the message and the fix", () => {
    renderWithReadiness([MISSING_REVIEWER]);
    const item = screen.getByTestId("dispatch-readiness-issue");
    expect(item).toHaveTextContent("acme/widget");
    expect(item).toHaveTextContent("reviewer_credential");
    expect(item).toHaveTextContent("no reviewer credential is stored");
    expect(item).toHaveTextContent("Fix: store a reviewer credential for acme/widget");
  });

  it("lists every failing requirement of every engine", () => {
    renderWithReadiness([MISSING_REVIEWER, UNKNOWN_QUALITY, UNCONFIGURED]);
    const items = within(screen.getByTestId("dispatch-readiness-banner")).getAllByTestId(
      "dispatch-readiness-issue",
    );
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("reviewer_credential");
    expect(items[1]).toHaveTextContent("code_quality");
    expect(items[2]).toHaveTextContent("acme/gadget");
    expect(items[2]).toHaveTextContent("default_engine");
    expect(items[2]).toHaveTextContent("Fix: ./setup.sh");
  });

  it("stays labelled Dispatch held when a not-ready issue sits beside unknown ones", () => {
    renderWithReadiness([UNKNOWN_QUALITY, MISSING_REVIEWER]);
    expect(screen.getByTestId("dispatch-readiness-banner")).toHaveTextContent("Dispatch held");
  });
});

describe("DispatchReadinessBanner — unverified", () => {
  it("is labelled Dispatch readiness unverified when every issue is unknown", () => {
    renderWithReadiness([UNKNOWN_QUALITY]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch readiness unverified");
    expect(banner).not.toHaveTextContent("Dispatch held");
    expect(banner).toHaveTextContent("could not be evaluated (OSError)");
  });
});

describe("DispatchReadinessBanner — warning", () => {
  it("is labelled Dispatch readiness warning when every issue is a warning", () => {
    renderWithReadiness([EXPIRY_WARNING]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch readiness warning");
    expect(banner).not.toHaveTextContent("Dispatch held");
    expect(banner).not.toHaveTextContent("Dispatch readiness unverified");
  });

  it("uses the amber warning-text tone for the warning-only heading", () => {
    renderWithReadiness([EXPIRY_WARNING]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    const heading = within(banner).getByText("Dispatch readiness warning");
    expect(heading.className).toContain("text-status-warning-fg");
  });

  it("names the expiry date and the fix in the warning row", () => {
    renderWithReadiness([EXPIRY_WARNING]);
    const item = screen.getByTestId("dispatch-readiness-issue");
    expect(item).toHaveTextContent("2031-02-03");
    expect(item).toHaveTextContent("Fix: store a reviewer credential for acme/widget");
  });

  it("stays labelled Dispatch held when a warning sits beside a blocking issue", () => {
    renderWithReadiness([EXPIRY_WARNING, MISSING_REVIEWER]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch held");
    expect(banner).not.toHaveTextContent("Dispatch readiness warning");
  });

  it("stays labelled Dispatch readiness unverified when a warning sits beside an unknown issue", () => {
    renderWithReadiness([EXPIRY_WARNING, UNKNOWN_QUALITY]);
    const banner = screen.getByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch readiness unverified");
    expect(banner).not.toHaveTextContent("Dispatch readiness warning");
  });

  it("keeps a blocking issue and a warning for the same requirement as distinct rows", () => {
    renderWithReadiness([IDENTICAL_REVIEWER, EXPIRY_WARNING]);
    const items = within(screen.getByTestId("dispatch-readiness-banner")).getAllByTestId(
      "dispatch-readiness-issue",
    );
    expect(items).toHaveLength(2);
  });
});

describe("DispatchReadinessBanner — provider wiring and recipe", () => {
  it("shows the issues published by the health response", async () => {
    server.use(
      http.get("/api/health", () =>
        HttpResponse.json({ ...HEALTHY, dispatch_readiness: [MISSING_REVIEWER] }),
      ),
    );
    render(
      <HealthProvider>
        <DispatchReadinessBanner />
      </HealthProvider>,
    );
    const banner = await screen.findByTestId("dispatch-readiness-banner");
    expect(banner).toHaveTextContent("Dispatch held");
    expect(banner).toHaveTextContent("no reviewer credential is stored");
  });

  it("shares the queue-stopped banner's container styling", () => {
    render(
      <HealthContext.Provider
        value={contextValue({
          queuePaused: true,
          pausedUntil: 4102444800,
          spendCapStop: { cause: "Spend limit reached.", fix: "Raise it.", regain_at: null },
          dispatchReadiness: [MISSING_REVIEWER],
        })}
      >
        <QueuePausedBanner />
        <DispatchReadinessBanner />
      </HealthContext.Provider>,
    );
    const stopped = screen.getByTestId("queue-stopped-banner");
    const readiness = screen.getByTestId("dispatch-readiness-banner");
    expect(readiness.className).toBe(stopped.className);
    expect(readiness).toHaveAttribute("role", stopped.getAttribute("role"));
    expect(readiness).toHaveAttribute("aria-live", stopped.getAttribute("aria-live"));
  });
});

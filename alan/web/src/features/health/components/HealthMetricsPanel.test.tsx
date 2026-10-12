import { render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { HealthScopeWindows, RepoHealthRow } from "../../../api";
import { HealthProvider } from "../../../api/HealthProvider";
import {
  METRICS_COMPUTED_AT,
  healthWindow,
  metricWindows,
  repoRow,
  scopeWindows,
} from "../../../test/healthWindows";
import { expectDisclosureState, expectTogglesOnEveryActivation } from "../../../test/disclosure";
import { server } from "../../../test/server";
import HealthMetricsPanel from "./HealthMetricsPanel";

const REPO = "DejvMatikar/alan";

const BASE_HEALTH = {
  ok: true as const,
  queue_paused: false,
  paused_until: null,
  health_state: null,
};

const FLEET = scopeWindows({
  outcome: metricWindows(healthWindow(0.9, 30), healthWindow(0.6, 90), "better"),
});

const SCOPED_ROW = repoRow(REPO, {
  in_flight: { value: 3, sample: 3, low_data: false },
  windows: scopeWindows({
    outcome: metricWindows(healthWindow(0.5, 30), healthWindow(0.55, 90), "same"),
  }),
});

interface RenderOptions {
  repoRow?: RepoHealthRow | null;
  fleet?: HealthScopeWindows | null;
  metricsComputedAt?: string | null;
  unattributed?: number;
  loaded?: boolean;
  error?: boolean;
}

function renderPanel(options: RenderOptions = {}) {
  const {
    repoRow: row = SCOPED_ROW,
    fleet = FLEET,
    metricsComputedAt = METRICS_COMPUTED_AT,
    unattributed = 0,
    loaded = true,
    error = false,
  } = options;
  return render(
    <HealthProvider>
      <HealthMetricsPanel
        repo={REPO}
        repoRow={row}
        fleet={fleet}
        metricsComputedAt={metricsComputedAt}
        unattributed={unattributed}
        loaded={loaded}
        error={error}
      />
    </HealthProvider>,
  );
}

function serveHealth(overrides: Record<string, unknown>) {
  server.use(http.get("/api/health", () => HttpResponse.json({ ...BASE_HEALTH, ...overrides })));
}

describe("HealthMetricsPanel — identity", () => {
  it("renders health-metrics-panel and never the retired tiles or health-card surfaces", () => {
    renderPanel();
    expect(screen.getByTestId("health-metrics-panel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Health metrics" })).toBeInTheDocument();
    expect(screen.queryByTestId("health-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("finisher-funnel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("metric-mean-fix-cycles")).not.toBeInTheDocument();
    expect(screen.queryByText(/mean fix cycles/i)).not.toBeInTheDocument();
  });

  it("renders the metrics as one table with the four columns", () => {
    renderPanel();
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(screen.getByTestId("repo-metrics-table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "7d vs 30d" })).toBeInTheDocument();
  });
});

describe("HealthMetricsPanel — scoped repository", () => {
  it("shows the repository's own windows and no fleet-wide fallback note", () => {
    renderPanel();
    expect(screen.getByTestId("repo-metric-outcome-7d")).toHaveTextContent("50%");
    expect(screen.getByTestId("repo-metric-outcome-30d")).toHaveTextContent("55%");
    expect(screen.queryByTestId("health-metrics-fleet-fallback")).not.toBeInTheDocument();
  });

  it("shows a right-now in-flight line from the repository row above the table", () => {
    renderPanel();
    const line = screen.getByTestId("health-metrics-in-flight");
    expect(line).toHaveTextContent("Right now: 3 in flight");
    const table = screen.getByTestId("repo-metrics-table");
    expect(line.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows a dash when the row has no in-flight count", () => {
    renderPanel({ repoRow: repoRow(REPO, { in_flight: { value: null, sample: 0, low_data: false } }) });
    expect(screen.getByTestId("health-metrics-in-flight")).toHaveTextContent("Right now: — in flight");
  });

  it("states that per-repository format error and correction recovery count only attributable records", () => {
    renderPanel();
    const note = screen.getByTestId("health-metrics-attribution-note");
    expect(note).toHaveTextContent(
      `Format error and correction recovery for ${REPO} count only records that can be attributed to a repository.`,
    );
    expect(note).not.toHaveTextContent("could not be attributed");
  });

  it("reports how many records could not be attributed", () => {
    renderPanel({ unattributed: 5 });
    expect(screen.getByTestId("health-metrics-attribution-note")).toHaveTextContent(
      "5 records in the last 30 days could not be attributed and appear only in the fleet-wide figures.",
    );
  });

  it("uses the singular for a single unattributed record", () => {
    renderPanel({ unattributed: 1 });
    expect(screen.getByTestId("health-metrics-attribution-note")).toHaveTextContent(
      "1 record in the last 30 days could not be attributed",
    );
  });
});

describe("HealthMetricsPanel — fleet-wide fallback", () => {
  it("keeps the fallback note and shows the fleet windows when the repository has no row", () => {
    renderPanel({ repoRow: null });
    expect(screen.getByTestId("health-metrics-fleet-fallback")).toHaveTextContent(
      `No repository-specific data yet for ${REPO} — figures below are fleet-wide.`,
    );
    expect(screen.getByTestId("repo-metric-outcome-7d")).toHaveTextContent("90%");
    expect(screen.getByTestId("repo-metric-outcome-30d")).toHaveTextContent("60%");
    expect(screen.queryByTestId("health-metrics-attribution-note")).not.toBeInTheDocument();
  });

  it("takes the right-now in-flight count from the fleet quality metrics", async () => {
    serveHealth({ quality_metrics: { in_flight_build_count: 4 } });
    renderPanel({ repoRow: null });
    await waitFor(() => {
      expect(screen.getByTestId("health-metrics-in-flight")).toHaveTextContent("Right now: 4 in flight");
    });
  });

  it("shows a dash for the in-flight count while the fleet quality metrics are unavailable", () => {
    renderPanel({ repoRow: null });
    expect(screen.getByTestId("health-metrics-in-flight")).toHaveTextContent("Right now: — in flight");
  });
});

describe("HealthMetricsPanel — freshness", () => {
  it("shows one keyboard-focusable updated line for the whole table", () => {
    renderPanel();
    const freshness = screen.getByTestId("health-freshness");
    expect(screen.getAllByText(/^Updated /)).toHaveLength(1);
    expect(freshness).toHaveAttribute("tabindex", "0");
    const describedBy = freshness.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")).not.toBeEmptyDOMElement();
  });

  it("omits the updated line when the metrics have no timestamp", () => {
    renderPanel({ metricsComputedAt: null });
    expect(screen.queryByTestId("health-freshness")).not.toBeInTheDocument();
  });

  it("labels an unparseable timestamp as unknown", () => {
    renderPanel({ metricsComputedAt: "not-a-date" });
    const freshness = screen.getByTestId("health-freshness");
    expect(within(freshness).getByText("Unknown", { selector: "span.sr-only" })).toBeInTheDocument();
  });
});

describe("HealthMetricsPanel — caveats disclosure", () => {
  it("keeps the longer caveats in a collapsed What these numbers mean section below the table", async () => {
    renderPanel();
    const disclosure = screen.getByTestId("health-metrics-caveats");
    await expectDisclosureState(disclosure, false);
    expect(disclosure.querySelector("summary")).toHaveTextContent("What these numbers mean");
    expect(within(disclosure).getByTestId("health-metrics-caveat-autonomous")).toHaveTextContent(
      "upper bound on autonomy",
    );
    const table = screen.getByTestId("repo-metrics-table");
    expect(table.compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("opens and closes the caveats by click, Enter and Space", async () => {
    renderPanel();
    await expectTogglesOnEveryActivation(screen.getByTestId("health-metrics-caveats"));
  });
});

describe("HealthMetricsPanel — loading and failure", () => {
  it("shows a loading message and no table while the windows have not loaded", () => {
    renderPanel({ repoRow: null, fleet: null, loaded: false });
    expect(screen.getByTestId("health-metrics-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-metrics-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("health-metrics-fleet-fallback")).not.toBeInTheDocument();
  });

  it("shows an error message and no table when loading finished without windows", () => {
    renderPanel({ repoRow: null, fleet: null, loaded: true });
    expect(screen.getByTestId("health-metrics-error")).toHaveTextContent(
      "Unable to load health metrics.",
    );
    expect(screen.queryByTestId("health-metrics-loading")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-metrics-table")).not.toBeInTheDocument();
  });

  it("replaces already-loaded metrics with the error message when a later refetch fails", () => {
    renderPanel({ loaded: true, error: true });
    expect(screen.getByTestId("health-metrics-error")).toHaveTextContent(
      "Unable to load health metrics.",
    );
    expect(screen.queryByTestId("repo-metrics-table")).not.toBeInTheDocument();
    expect(screen.queryByTestId("health-metrics-in-flight")).not.toBeInTheDocument();
    expect(screen.queryByTestId("health-metrics-caveats")).not.toBeInTheDocument();
  });
});

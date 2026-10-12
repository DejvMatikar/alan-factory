import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

const DEFAULT_ENGINE_KEY = "DejvMatikar/alan";

const STUBBED_DIGEST: Record<string, unknown> = {
  recovery: { current_count: 6, previous_count: 3, direction: "up" },
  residue: [
    { fingerprint: "a1b2c3d4e5f6a7b8", title: "Unclassified anomaly — needs a typed handler" },
  ],
  digest_overdue: false,
};

async function stubDigest(page: Page, body: Record<string, unknown> = STUBBED_DIGEST) {
  await page.route("**/api/health/digest", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({ json: body });
  });
}

test.describe("Health — daily digest signal", { tag: "@no-claude" }, () => {
  test("shows the recovery figure and trend, and the residue count, on the fleet-wide view", async ({
    page,
  }) => {
    await stubDigest(page);
    await page.goto("/health");
    await expect(page.getByTestId("digest-signal-section")).toBeVisible();
    await expect(page.getByTestId("metric-digest-recovery")).toContainText("6");
    await expect(page.getByTestId("metric-digest-recovery-trend")).toContainText("up");
    await expect(page.getByTestId("metric-digest-residue")).toContainText("1");
  });

  test("does not fetch the digest or render either signal on the repository-scoped view", async ({
    page,
  }) => {
    let digestRequested = false;
    await page.route("**/api/health/digest", async (route) => {
      digestRequested = true;
      await route.fulfill({ json: STUBBED_DIGEST });
    });
    await page.goto(`/health?engine=${encodeURIComponent(DEFAULT_ENGINE_KEY)}`);
    await expect(page.getByTestId("health-metrics-panel")).toBeVisible();
    await expect(page.getByTestId("digest-signal-section")).toHaveCount(0);
    expect(digestRequested).toBe(false);
  });

  test("renders a not-measured-yet state instead of a zero when no digest has ever been produced", async ({
    page,
  }) => {
    await stubDigest(page, { recovery: null, residue: null, digest_overdue: true });
    await page.goto("/health");
    await expect(page.getByTestId("metric-digest-recovery")).toContainText("not measured yet");
    await expect(page.getByTestId("metric-digest-residue")).toContainText("not measured yet");
    await expect(page.getByTestId("aggregate-chip-digest-sweep")).toContainText("Overdue");
  });

  test("renders a zero residue count distinctly from not-measured-yet when the window is clean", async ({
    page,
  }) => {
    await stubDigest(page, {
      recovery: { current_count: 0, previous_count: 0, direction: "same" },
      residue: [],
      digest_overdue: false,
    });
    await page.goto("/health");
    await expect(page.getByTestId("metric-digest-residue")).toContainText("0");
    await expect(page.getByTestId("metric-digest-residue")).not.toContainText(
      "not measured yet",
    );
  });
});

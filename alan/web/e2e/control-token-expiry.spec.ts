import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

interface StatusStub {
  state: "active" | "expired" | "revoked";
  deadline: string | null;
}

async function stubControlTokenStatus(page: Page, status: StatusStub): Promise<void> {
  await page.route("**/api/control-token/status", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({ json: status });
  });
}

test(
  "Settings renders the expired server state distinctly from active, naming the remedy",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubControlTokenStatus(page, { state: "expired", deadline: "2026-07-01T00:00:00+00:00" });
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    const serverLine = page.getByTestId("control-token-server-status");
    await expect(serverLine).toContainText("expired");
    await expect(serverLine).not.toContainText("2026-07-01T00:00:00+00:00");
    await expect(serverLine).toContainText("paste the new value into every browser");
    await expect(serverLine).not.toContainText("active,");
  },
);

test(
  "Settings renders the revoked server state distinctly, with no deadline to show",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubControlTokenStatus(page, { state: "revoked", deadline: null });
    await page.goto("/");
    await page.getByTestId("settings-gear").click();
    const serverLine = page.getByTestId("control-token-server-status");
    await expect(serverLine).toContainText("revoked");
  },
);

test(
  "GET /api/control-token/status is reachable with no credential and never returns the token value",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.get("/api/control-token/status");
    expect(res.status()).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(["deadline", "state"]);
    expect(JSON.stringify(body)).not.toMatch(/[0-9a-f]{32,}/);
  },
);

test(
  "GET /api/health is reachable with no credential — the credential-expiry finding never depends on one",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.get("/api/health");
    expect(res.status()).toBe(200);
  },
);

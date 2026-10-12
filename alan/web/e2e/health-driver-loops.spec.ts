import { expect, test } from "./fixtures/test-base";

test(
  "driver-loops endpoint responds 200 with supervised=false and a loops array in this environment",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.get("/api/driver-loops");
    expect(res.status()).toBe(200);
    const data = (await res.json()) as { supervised: boolean; loops: unknown[] };
    expect(data.supervised).toBe(false);
    expect(Array.isArray(data.loops)).toBe(true);
  },
);

test(
  "driver-loops pause without a control token is refused with 401",
  { tag: "@no-claude" },
  async ({ request }) => {
    const res = await request.post("/api/driver-loops/alan-issue-driver/pause");
    expect(res.status()).toBe(401);
  },
);

test(
  "renders the Driver loops panel on the unscoped health page with no mutation controls",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    const panel = page.getByTestId("driver-loops-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByRole("heading", { name: "Driver loops" })).toBeVisible();
    const empty = page.getByTestId("driver-loops-empty");
    await expect(empty).toHaveText(
      "Not supervised on this instance — driver loops run only in production.",
    );
    await expect(empty).not.toContainText("enabling a headless loop waits");
    await expect(page.locator('[data-testid^="driver-loop-pause-resume-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="driver-loop-run-now-"]')).toHaveCount(0);
  },
);

test(
  "renders no Driver loops panel once ?engine= scopes the health page",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health?engine=DejvMatikar%2Falan");
    await expect(page.getByTestId("driver-loops-panel")).toHaveCount(0);
  },
);

test(
  "shows the configured-empty message and note, with no controls, when supervised with zero loops",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.route("**/api/driver-loops", (route) =>
      route.fulfill({ json: { supervised: true, loops: [] } }),
    );
    await page.goto("/health");
    const panel = page.getByTestId("driver-loops-panel");
    await expect(panel).toBeVisible();
    await expect(page.getByTestId("driver-loops-empty")).toHaveText(
      "No driver loops configured. The drivers run interactively today; enabling a headless loop waits on the open write-gap gate.",
    );
    await expect(page.locator('[data-testid^="driver-loop-pause-resume-"]')).toHaveCount(0);
    await expect(page.locator('[data-testid^="driver-loop-run-now-"]')).toHaveCount(0);
  },
);

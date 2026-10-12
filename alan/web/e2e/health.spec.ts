import { expect, test } from "./fixtures/test-base";

test("health endpoint responds 200 with ok payload", { tag: "@no-claude" }, async ({ request }) => {
  const res = await request.get("/api/health");
  expect(res.status()).toBe(200);
  const data = await res.json() as { ok: boolean; queue_paused: boolean; health_state: unknown };
  expect(data.ok).toBe(true);
  expect(typeof data.queue_paused).toBe("boolean");
  expect("health_state" in data).toBe(true);
});

test("health endpoint health_state is null or an object", { tag: "@no-claude" }, async ({ request }) => {
  const res = await request.get("/api/health");
  const data = await res.json() as { health_state: null | { verdict: string; blocked_count: number } };
  if (data.health_state !== null) {
    expect(typeof data.health_state.verdict).toBe("string");
    expect(typeof data.health_state.blocked_count).toBe("number");
  }
});

test(
  "renders the recurring-processes detail once the health page is scoped to a repository",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health?engine=DejvMatikar%2Falan");
    await expect(page.getByTestId("recurring-processes-panel-DejvMatikar/alan")).toBeVisible();
  },
);

test(
  "renders no recurring-processes detail on the unscoped health page",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    await expect(page.getByTestId("repo-health-panel")).toBeVisible();
    await expect(page.locator('[data-testid^="recurring-processes-panel-"]')).toHaveCount(0);
  },
);

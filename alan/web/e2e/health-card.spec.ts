import { expect, test } from "./fixtures/test-base";
import { seedProjectFeatures } from "./fixtures/seed";

test.beforeEach(async ({ page }) => {
  await seedProjectFeatures(page, true);
});

test(
  "nav-health-badge is present in the page header banner on the home page",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("banner").getByTestId("nav-health-badge")).toBeVisible();
  },
);

test(
  "nav-health-badge is present in the page header banner on another route",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("banner").getByTestId("nav-health-badge")).toBeVisible();
  },
);

test(
  "nav-health-badge links to /health",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    const badge = page.getByRole("banner").getByTestId("nav-health-badge");
    await expect(badge).toHaveAttribute("href", "/health");
  },
);

test(
  "clicking nav-health-badge navigates to the health view",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("banner").getByTestId("nav-health-badge").click();
    await expect(page).toHaveURL(/\/health$/);
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
  },
);

test(
  "health view shows stale notice when no sweep has run",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");
    await expect(page.getByRole("heading", { name: "System Health" })).toBeVisible();
  },
);

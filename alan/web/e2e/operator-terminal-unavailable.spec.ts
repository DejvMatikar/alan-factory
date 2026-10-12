import { expect, test } from "./fixtures/test-base";

interface StatusBody {
  session: unknown;
  unavailable: { reason: string; detail: string } | null;
}

test(
  "the Operator page names detached mode as the cause instead of showing a terminal",
  { tag: "@no-claude" },
  async ({ page, request }) => {
    const response = await request.get("/api/operator/status");
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as StatusBody;
    expect(body.session).toBeNull();
    expect(body.unavailable?.reason).toBe("detached_mode_off");

    await page.goto("/operator");

    await expect(page.getByRole("heading", { level: 1, name: "Operator" })).toBeVisible();
    await expect(page.getByTestId("operator-unavailable")).toBeVisible();
    await expect(page.getByTestId("operator-unavailable-cause")).toHaveText(
      "Detached worker mode is off",
    );
    await expect(page.getByTestId("operator-unavailable-detail")).toHaveText(
      body.unavailable?.detail ?? "",
    );
    await expect(page.getByTestId("operator-terminal")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("operator-target")).toHaveCount(0);
  },
);

test(
  "a start the service refuses as unavailable names the cause in the dialog and keeps it open",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.route("**/api/operator/status", async (route) => {
      await route.fulfill({ json: { session: null, unavailable: null } });
    });
    await page.route("**/api/operator/session", async (route) => {
      await route.fulfill({
        status: 503,
        json: { detail: { reason: "tmux_missing", detail: "tmux is not installed." } },
      });
    });
    await page.goto("/operator");

    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await page.getByTestId("operator-start-submit").click();

    await expect(page.getByTestId("operator-start-error")).toHaveText("tmux is not installed.");
    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await expect(page.getByTestId("operator-start-submit")).toBeEnabled();
  },
);

test(
  "the top-bar Operator entry shows no lit dot when no session is live and links to the page",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.goto("/health");

    const entry = page.getByRole("banner").getByTestId("nav-operator");
    await expect(entry).toBeVisible();
    await expect(entry).toHaveAttribute("aria-label", "Operator terminal");
    await expect(page.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "false");

    await entry.click();

    await expect(page).toHaveURL((url) => url.pathname === "/operator");
    await expect(page.getByTestId("operator-unavailable")).toBeVisible();
  },
);

test(
  "at phone width the Operator page asks for a larger screen instead of a terminal",
  { tag: "@no-claude" },
  async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/operator");

    await expect(page.getByTestId("operator-phone-notice")).toContainText("needs a larger screen");
    await expect(page.getByTestId("operator-terminal")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId("operator-unavailable")).toHaveCount(0);
  },
);

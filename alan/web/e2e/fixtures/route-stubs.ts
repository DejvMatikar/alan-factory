import type { Page } from "@playwright/test";

export async function stubDefaultProjectWorld(page: Page): Promise<void> {
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({ json: [] });
  });
}

export async function stubDefaultEngineWorld(page: Page): Promise<void> {
  await page.route("**/api/engines", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({ json: { default_engine_key: "DejvMatikar/alan", engines: [] } });
  });
}

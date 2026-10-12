import type { Page, WebSocketRoute } from "@playwright/test";

import { expect, test } from "./fixtures/test-base";

const SESSION = {
  scope: "fleet",
  target: null,
  started_at: "2026-09-29T10:00:00Z",
  update_warning: null,
  exited: false,
};

interface OperatorStub {
  live: boolean;
  tickets: number;
  starts: unknown[];
  ends: number;
  sockets: WebSocketRoute[];
  received: (string | Buffer)[];
}

interface ResizeFrame {
  type: string;
  cols: number;
  rows: number;
}

async function stubOperator(page: Page, live: boolean): Promise<OperatorStub> {
  const stub: OperatorStub = { live, tickets: 0, starts: [], ends: 0, sockets: [], received: [] };
  await page.route("**/api/operator/**", async (route) => {
    const request = route.request();
    const { pathname } = new URL(request.url());
    if (pathname === "/api/operator/status") {
      await route.fulfill({
        json: { session: stub.live ? SESSION : null, unavailable: null },
      });
    } else if (pathname === "/api/operator/attach-ticket") {
      stub.tickets += 1;
      await route.fulfill({ json: { ticket: `stub-${String(stub.tickets)}`, expires_in_s: 30 } });
    } else if (pathname === "/api/operator/session" && request.method() === "POST") {
      stub.starts.push(request.postDataJSON() as unknown);
      stub.live = true;
      await route.fulfill({ json: SESSION });
    } else if (pathname === "/api/operator/session" && request.method() === "DELETE") {
      stub.ends += 1;
      stub.live = false;
      await route.fulfill({ status: 204 });
    } else {
      await route.fallback();
    }
  });
  await page.routeWebSocket(/\/api\/operator\/terminal/, (socket) => {
    stub.sockets.push(socket);
    socket.onMessage((message) => {
      stub.received.push(message);
    });
    socket.send(Buffer.from("operator ready\r\n"));
  });
  return stub;
}

function typedText(stub: OperatorStub): string {
  return stub.received
    .filter((message): message is Buffer => typeof message !== "string")
    .map((message) => message.toString("utf8"))
    .join("");
}

function resizeFrames(stub: OperatorStub): ResizeFrame[] {
  return stub.received
    .filter((message): message is string => typeof message === "string")
    .map((message) => JSON.parse(message) as ResizeFrame)
    .filter((frame) => frame.type === "resize");
}

function workspaceRow(taskId: string, status: string, operatorLink: boolean) {
  return {
    task_id: taskId,
    title: `Title of ${taskId}`,
    status,
    created_at: "2026-09-29T09:00:00Z",
    updated_at: "2026-09-29T09:30:00Z",
    team_runs_total: 0,
    invocations_total: 0,
    tasks_done: 0,
    tasks_total: 0,
    engine_key: "acme/widget",
    operator_link: operatorLink,
  };
}

async function stubTargets(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === "/api/engines",
    async (route) => {
      await route.fulfill({
        json: {
          engines: [
            { repo: { owner: "acme", repo: "widget" }, posture: "autonomous" },
            { repo: { owner: "acme", repo: "gadget" }, posture: "autonomous" },
          ],
        },
      });
    },
  );
  await page.route(
    (url) => url.pathname === "/api/workspaces",
    async (route) => {
      await route.fulfill({
        json: [
          workspaceRow("fix-it", "escalated", true),
          workspaceRow("humming", "active", false),
          workspaceRow("broken", "failed", true),
        ],
      });
    },
  );
}

async function openAttachedPage(page: Page, live = true): Promise<OperatorStub> {
  const stub = await stubOperator(page, live);
  await page.goto("/operator");
  await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
  return stub;
}

test(
  "starts a global session from the picker, shows its output and sends keystrokes to it",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await stubOperator(page, false);
    await page.goto("/operator");

    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await page.getByTestId("operator-start-submit").click();

    await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
    expect(stub.starts).toEqual([{ scope: "fleet", target: null, replace: false }]);
    const terminal = page.getByTestId("operator-terminal");
    await expect(terminal).toContainText("operator ready");
    await expect(terminal.locator(".xterm-accessibility-tree")).toHaveCount(1);

    await terminal.click();
    await page.keyboard.type("hello");
    await expect.poll(() => typedText(stub)).toContain("hello");
  },
);

test(
  "the repository scope offers a dropdown of connected repositories and starts only once one is chosen",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await stubOperator(page, false);
    await stubTargets(page);
    await page.goto("/operator");

    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await page.getByTestId("operator-scope-repo").check();
    const target = page.getByLabel("Repository", { exact: true });
    await expect(target).toBeEnabled();
    await expect(target.locator("option")).toHaveText([
      "Choose a repository",
      "acme/gadget",
      "acme/widget",
    ]);
    await expect(page.getByTestId("operator-start-submit")).toBeDisabled();

    await target.selectOption("acme/widget");
    await expect(page.getByTestId("operator-start-submit")).toBeEnabled();
    await page.getByTestId("operator-start-submit").click();

    await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
    expect(stub.starts).toEqual([{ scope: "repo", target: "acme/widget", replace: false }]);
  },
);

test(
  "the workspace scope lists only stuck workspaces with their status and repository",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await stubOperator(page, false);
    await stubTargets(page);
    await page.goto("/operator");

    await page.getByTestId("operator-scope-workspace").check();
    const target = page.getByLabel("Workspace", { exact: true });
    await expect(target).toBeEnabled();
    await expect(target.locator("option")).toHaveText([
      "Choose a workspace",
      "Title of fix-it · escalated · acme/widget",
      "Title of broken · failed · acme/widget",
    ]);
    await expect(page.getByTestId("operator-start-submit")).toBeDisabled();

    await target.selectOption("broken");
    await page.getByTestId("operator-start-submit").click();

    await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
    expect(stub.starts).toEqual([{ scope: "workspace", target: "broken", replace: false }]);
  },
);

test(
  "the workspace scope says nothing needs the operator and blocks Start when no workspace is stuck",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubOperator(page, false);
    await page.route(
      (url) => url.pathname === "/api/workspaces",
      async (route) => {
        await route.fulfill({ json: [workspaceRow("humming", "active", false)] });
      },
    );
    await page.goto("/operator");

    await page.getByTestId("operator-scope-workspace").check();

    await expect(page.getByTestId("operator-target-empty")).toHaveText(
      "Nothing needs the operator right now.",
    );
    await expect(page.getByLabel("Workspace", { exact: true })).toBeDisabled();
    await expect(page.getByTestId("operator-start-submit")).toBeDisabled();
  },
);

test(
  "a workspace list that cannot be loaded shows an inline error and blocks Start",
  { tag: "@no-claude" },
  async ({ page }) => {
    await stubOperator(page, false);
    await page.route(
      (url) => url.pathname === "/api/workspaces",
      async (route) => {
        await route.fulfill({ status: 500, json: { detail: "boom" } });
      },
    );
    await page.goto("/operator");

    await page.getByTestId("operator-scope-workspace").check();

    await expect(page.getByTestId("operator-target-error")).toHaveText(
      "The workspace list could not be loaded.",
    );
    await expect(page.getByTestId("operator-start-submit")).toBeDisabled();
  },
);

test(
  "resizing the window refits the terminal and tells the session its new size",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await page.setViewportSize({ width: 1000, height: 700 });
    await expect.poll(() => resizeFrames(stub).length).toBeGreaterThan(0);
    const narrow = resizeFrames(stub).at(-1)!;
    expect(narrow.cols).toBeGreaterThan(0);
    expect(narrow.rows).toBeGreaterThan(0);

    await page.setViewportSize({ width: 1400, height: 900 });
    await expect.poll(() => resizeFrames(stub).at(-1)?.cols ?? 0).toBeGreaterThan(narrow.cols);
  },
);

test(
  "Close leaves the session running with the top-bar dot lit, and reopening reattaches to it",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await page.getByTestId("operator-close").click();

    await expect(page).toHaveURL((url) => url.pathname === "/");
    await expect(page.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "true");
    expect(stub.ends).toBe(0);

    await page.getByTestId("nav-operator").click();

    await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
    await expect.poll(() => stub.sockets.length).toBe(2);
    await expect(page.getByTestId("operator-terminal")).toContainText("operator ready");
  },
);

test(
  "Close works from the keyboard",
  { tag: "@no-claude" },
  async ({ page }) => {
    await openAttachedPage(page);

    await page.getByTestId("operator-close").focus();
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL((url) => url.pathname === "/");
  },
);

test(
  "End session asks for confirmation, then stops the session and puts the dot out",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await page.getByTestId("operator-end").click();
    const dialog = page.getByTestId("operator-end-dialog");
    await expect(dialog).toBeVisible();
    expect(stub.ends).toBe(0);
    await dialog.getByRole("button", { name: "End session" }).click();

    await expect(page.getByTestId("operator-notice-ended")).toBeVisible();
    expect(stub.ends).toBe(1);
    await expect(page.getByTestId("operator-terminal")).toHaveCount(0);
    await expect(page.getByTestId("nav-operator-dot")).toHaveAttribute("data-live", "false");
  },
);

test(
  "End session works from the keyboard and Cancel leaves the session running",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await page.getByTestId("operator-end").focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByTestId("operator-end-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(stub.ends).toBe(0);

    await page.getByTestId("operator-end").focus();
    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Control+Enter");

    await expect(page.getByTestId("operator-notice-ended")).toBeVisible();
    expect(stub.ends).toBe(1);
  },
);

test(
  "Ctrl+Shift+F moves focus from the terminal to the controls outside it",
  { tag: "@no-claude" },
  async ({ page }) => {
    await openAttachedPage(page);
    await expect(page.getByTestId("operator-escape-hint")).toContainText(/Ctrl Shift F|⌃⇧F/);

    await page.getByTestId("operator-terminal").click();
    await page.keyboard.press("Control+Shift+F");

    await expect(page.getByTestId("operator-close")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByTestId("operator-end")).toBeFocused();
  },
);

test(
  "reattaches with a fresh ticket after the service closes the connection",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);
    expect(stub.tickets).toBe(1);

    await stub.sockets[0]!.close({ code: 1012, reason: "service restart" });

    await expect.poll(() => stub.sockets.length).toBe(2);
    expect(stub.tickets).toBe(2);
    await expect(page.getByTestId("operator-notice-attached")).toBeVisible();
    await expect(page.getByTestId("operator-terminal")).toContainText("operator ready");
  },
);

test(
  "an attachment the server supersedes says so, drops its terminal, and does not reconnect",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await stub.sockets[0]!.close({ code: 4001, reason: "superseded" });

    await expect(page.getByTestId("operator-notice-superseded")).toBeVisible();
    await expect(page.getByTestId("operator-terminal")).toHaveCount(0);
    expect(stub.sockets).toHaveLength(1);
    expect(stub.tickets).toBe(1);
  },
);

test(
  "starting while a session is live needs an explicit replace",
  { tag: "@no-claude" },
  async ({ page }) => {
    const stub = await openAttachedPage(page);

    await page.getByTestId("operator-replace").click();
    await expect(page.getByTestId("operator-scope-dialog")).toBeVisible();
    await expect(page.getByTestId("operator-start-submit")).toBeDisabled();
    await page.getByTestId("operator-replace-confirm").check();
    await page.getByTestId("operator-start-submit").click();

    await expect(page.getByTestId("operator-scope-dialog")).toBeHidden();
    expect(stub.starts).toEqual([{ scope: "fleet", target: null, replace: true }]);
  },
);
